import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from icalendar import Calendar, Event

from notifications.services import (
    notify_cancellation,
    notify_decision,
    notify_decision_summary,
    notify_deletion,
    notify_new_request,
)
from places.models import Place, PlaceClosure
from units.models import Unit

from .models import CalendarFeed, RequestStatusHistory, SessionRequest

SLOT_MINUTES = 30
MIN_DURATION_MINUTES = 30
MAX_DURATION_MINUTES = 240


def _local_tz():
    return ZoneInfo(settings.TIME_ZONE)


def _ensure_aware(value):
    if timezone.is_naive(value):
        return timezone.make_aware(value, _local_tz())
    return value


def _validate_request_time(place, start_at, end_at):
    start_at = _ensure_aware(start_at)
    end_at = _ensure_aware(end_at)

    if not place.is_active:
        raise ValidationError("This place is not available for booking.")
    if end_at <= start_at:
        raise ValidationError("End time must be later than start time.")

    local_start = timezone.localtime(start_at)
    local_end = timezone.localtime(end_at)

    if local_start.date() != local_end.date():
        raise ValidationError("Sessions must start and end on the same date.")
    if local_start.minute % SLOT_MINUTES or local_start.second or local_start.microsecond:
        raise ValidationError("Start time must align to a 30-minute slot.")
    if start_at < timezone.now():
        raise ValidationError("The requested start time is in the past.")
    if local_start.time() < place.opening_time:
        raise ValidationError("The requested time is before the place opens.")
    if local_end.time() > place.closing_time:
        raise ValidationError("The requested time is after the place closes.")

    closed = PlaceClosure.objects.filter(
        place=place,
        start_date__lte=local_start.date(),
        end_date__gte=local_start.date(),
    ).exists()
    if closed:
        raise ValidationError("The place is closed on the requested date.")

    duration = int((end_at - start_at).total_seconds() // 60)
    if duration < MIN_DURATION_MINUTES:
        raise ValidationError(f"Sessions must be at least {MIN_DURATION_MINUTES} minutes.")
    if duration > MAX_DURATION_MINUTES:
        raise ValidationError(f"Sessions must be at most {MAX_DURATION_MINUTES} minutes.")
    if duration % SLOT_MINUTES:
        raise ValidationError("Duration must be in 30-minute increments.")


def _derive_units(user, requesting_unit=None):
    if user.is_superuser:
        unit = requesting_unit or user.unit
        if not unit:
            raise ValidationError("Choose the unit this request is for.")
    else:
        if not user.unit_id:
            raise ValidationError("Your account is not linked to a unit.")
        if requesting_unit and requesting_unit.pk != user.unit_id:
            raise PermissionDenied("You can only request for your own unit.")
        unit = user.unit
        if user.is_board_account and unit.kind != Unit.Kind.BOARD:
            raise PermissionDenied("Board accounts must belong to a board.")
        if user.is_officer and unit.kind == Unit.Kind.BOARD:
            raise PermissionDenied("Officer accounts must belong to an entity.")

    if not unit.is_active:
        raise ValidationError("This unit is inactive.")

    if unit.kind == Unit.Kind.BOARD:
        if not unit.parent_id:
            raise ValidationError("This board is not assigned to a branch or chapter.")
        reviewer_unit = unit.parent
    else:
        reviewer_unit = unit

    return unit, reviewer_unit


@transaction.atomic
def create_session_request(
    *,
    user,
    place,
    start_at,
    end_at,
    title,
    purpose,
    requesting_unit=None,
):
    start_at = _ensure_aware(start_at)
    end_at = _ensure_aware(end_at)
    unit, reviewer_unit = _derive_units(user, requesting_unit)

    if not title.strip():
        raise ValidationError("A title is required.")
    if not purpose.strip():
        raise ValidationError("A purpose is required.")

    _validate_request_time(place, start_at, end_at)

    session_request = SessionRequest.objects.create(
        requesting_unit=unit,
        reviewer_unit=reviewer_unit,
        submitted_by=user,
        place=place,
        title=title.strip(),
        purpose=purpose.strip(),
        start_at=start_at,
        end_at=end_at,
        status=SessionRequest.Status.PENDING,
    )
    RequestStatusHistory.objects.create(
        request=session_request,
        from_status="",
        to_status=SessionRequest.Status.PENDING,
        changed_by=user,
        reason="Request created",
    )
    notify_new_request(session_request)
    return session_request


@transaction.atomic
def approve_session_request(*, request_id, reviewer, notify=True):
    if not (reviewer.is_superuser or reviewer.is_officer):
        raise PermissionDenied("Only officers can approve requests.")

    session_request = SessionRequest.objects.select_for_update().get(pk=request_id)
    if session_request.status != SessionRequest.Status.PENDING:
        raise ValidationError("Only pending requests can be approved.")

    place = Place.objects.select_for_update().get(pk=session_request.place_id)
    _validate_request_time(place, session_request.start_at, session_request.end_at)

    conflict = (
        SessionRequest.objects.filter(
            place=place,
            status=SessionRequest.Status.APPROVED,
            start_at__lt=session_request.end_at,
            end_at__gt=session_request.start_at,
        )
        .exclude(pk=session_request.pk)
        .exists()
    )
    if conflict:
        raise ValidationError("This place is already booked for that time.")

    old_status = session_request.status
    session_request.status = SessionRequest.Status.APPROVED
    session_request.reviewer = reviewer
    session_request.reviewed_at = timezone.now()
    session_request.save(update_fields=["status", "reviewer", "reviewed_at", "updated_at"])
    RequestStatusHistory.objects.create(
        request=session_request,
        from_status=old_status,
        to_status=SessionRequest.Status.APPROVED,
        changed_by=reviewer,
        reason="Approved",
    )
    if notify:
        notify_decision(session_request)
    return session_request


@transaction.atomic
def reject_session_request(*, request_id, reviewer, reason, notify=True):
    if not (reviewer.is_superuser or reviewer.is_officer):
        raise PermissionDenied("Only officers can reject requests.")
    if not reason.strip():
        raise ValidationError("A rejection reason is required.")

    session_request = SessionRequest.objects.select_for_update().get(pk=request_id)
    if session_request.status != SessionRequest.Status.PENDING:
        raise ValidationError("Only pending requests can be rejected.")

    old_status = session_request.status
    session_request.status = SessionRequest.Status.REJECTED
    session_request.reviewer = reviewer
    session_request.reviewed_at = timezone.now()
    session_request.decision_reason = reason.strip()
    session_request.save(
        update_fields=[
            "status",
            "reviewer",
            "reviewed_at",
            "decision_reason",
            "updated_at",
        ]
    )
    RequestStatusHistory.objects.create(
        request=session_request,
        from_status=old_status,
        to_status=SessionRequest.Status.REJECTED,
        changed_by=reviewer,
        reason=reason.strip(),
    )
    if notify:
        notify_decision(session_request)
    return session_request


@transaction.atomic
def cancel_session_request(*, request_id, actor, reason="", notify=True):
    session_request = SessionRequest.objects.select_for_update().get(pk=request_id)

    if not can_cancel_request(actor, session_request):
        raise PermissionDenied("You cannot cancel this request.")
    if session_request.status not in {
        SessionRequest.Status.PENDING,
        SessionRequest.Status.APPROVED,
    }:
        raise ValidationError("Only pending or approved requests can be cancelled.")
    if session_request.status == SessionRequest.Status.APPROVED and not reason.strip():
        raise ValidationError("A cancellation reason is required for approved requests.")

    old_status = session_request.status
    session_request.status = SessionRequest.Status.CANCELLED
    session_request.save(update_fields=["status", "updated_at"])
    RequestStatusHistory.objects.create(
        request=session_request,
        from_status=old_status,
        to_status=SessionRequest.Status.CANCELLED,
        changed_by=actor,
        reason=reason.strip() or "Cancelled",
    )
    if notify:
        notify_cancellation(session_request, reason=reason.strip())
    return session_request


def get_conflicting_requests(session_request):
    return SessionRequest.objects.filter(
        place=session_request.place,
        status=SessionRequest.Status.APPROVED,
        start_at__lt=session_request.end_at,
        end_at__gt=session_request.start_at,
    ).exclude(pk=session_request.pk)


def visible_requests_for(user):
    if not user.is_authenticated:
        return SessionRequest.objects.none()
    if user.is_superuser or user.is_officer:
        return SessionRequest.objects.all()
    if not user.unit_id:
        return SessionRequest.objects.none()
    return SessionRequest.objects.filter(requesting_unit=user.unit)


def can_view_request(user, session_request):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_officer:
        return True
    return session_request.requesting_unit_id == user.unit_id


def can_approve_request(user, session_request):
    return user.is_authenticated and (user.is_superuser or user.is_officer)


def can_cancel_request(user, session_request):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_officer:
        return True
    return session_request.requesting_unit_id == user.unit_id


def get_available_slots(place, on_date):
    if not place.is_active or place.opening_time >= place.closing_time:
        return []
    if on_date < timezone.localdate():
        return []

    closed = PlaceClosure.objects.filter(
        place=place,
        start_date__lte=on_date,
        end_date__gte=on_date,
    ).exists()
    if closed:
        return []

    tz = _local_tz()
    cursor = datetime.combine(on_date, place.opening_time, tzinfo=tz)
    closing = datetime.combine(on_date, place.closing_time, tzinfo=tz)

    bookings = list(
        SessionRequest.objects.filter(
            place=place,
            status=SessionRequest.Status.APPROVED,
            start_at__lt=closing,
            end_at__gt=cursor,
        ).values_list("start_at", "end_at")
    )

    now = timezone.localtime()
    slots = []
    while cursor + timedelta(minutes=SLOT_MINUTES) <= closing:
        slot_end = cursor + timedelta(minutes=SLOT_MINUTES)
        available = not any(
            booking_start < slot_end and booking_end > cursor
            for booking_start, booking_end in bookings
        )
        if on_date == now.date() and cursor < now:
            available = False
        slots.append(
            {
                "start": cursor.isoformat(),
                "end": slot_end.isoformat(),
                "available": available,
            }
        )
        cursor = slot_end

    return slots


def clone_initial(user, source):
    """Return prefilled form values for a request the user is allowed to see."""
    if not can_view_request(user, source):
        raise PermissionDenied("You cannot clone this request.")

    initial = {
        "place": source.place_id,
        "title": source.title,
        "purpose": source.purpose,
        "duration_minutes": source.duration_minutes,
    }
    if user.is_superuser:
        initial["requesting_unit"] = source.requesting_unit_id
    return initial


def find_next_available_slots(
    *,
    place,
    duration_minutes,
    after=None,
    horizon_days=60,
    limit=3,
):
    """Return non-overlapping free windows for a place, starting at or after ``after``."""
    duration_minutes = int(duration_minutes)
    if duration_minutes < MIN_DURATION_MINUTES or duration_minutes > MAX_DURATION_MINUTES:
        raise ValidationError(
            f"Duration must be between {MIN_DURATION_MINUTES} and "
            f"{MAX_DURATION_MINUTES} minutes."
        )
    if duration_minutes % SLOT_MINUTES:
        raise ValidationError("Duration must be in 30-minute increments.")

    needed_slots = duration_minutes // SLOT_MINUTES
    now = timezone.now()
    after = _ensure_aware(after) if after else now
    after = max(after, now)
    first_day = timezone.localtime(after).date()

    results = []
    for offset in range(horizon_days):
        on_date = first_day + timedelta(days=offset)
        slots = get_available_slots(place, on_date)
        index = 0
        while index + needed_slots <= len(slots):
            window = slots[index : index + needed_slots]
            window_start = datetime.fromisoformat(window[0]["start"])
            if window_start < after:
                index += 1
                continue
            if all(slot["available"] for slot in window):
                results.append(
                    {
                        "date": on_date.isoformat(),
                        "start": window_start.isoformat(),
                        "end": datetime.fromisoformat(window[-1]["end"]).isoformat(),
                    }
                )
                if len(results) >= limit:
                    return results
                index += needed_slots
            else:
                index += 1

    return results


def find_next_available_slot(*, place, duration_minutes, after=None, horizon_days=60):
    """Return the first free window for a place, or ``None`` when none exists."""
    slots = find_next_available_slots(
        place=place,
        duration_minutes=duration_minutes,
        after=after,
        horizon_days=horizon_days,
        limit=1,
    )
    return slots[0] if slots else None


def bulk_decide(*, request_ids, action, reviewer, reason=""):
    """Approve or reject several requests, reporting per-row failures."""
    if not (reviewer.is_superuser or reviewer.is_officer):
        raise PermissionDenied("Only officers can review requests.")
    if action not in {"approve", "reject"}:
        raise ValidationError("Choose approve or reject.")
    if action == "reject" and not reason.strip():
        raise ValidationError("A rejection reason is required.")

    succeeded = []
    succeeded_requests = []
    failed = []
    for request_id in request_ids:
        try:
            if action == "approve":
                session_request = approve_session_request(
                    request_id=request_id,
                    reviewer=reviewer,
                    notify=False,
                )
            else:
                session_request = reject_session_request(
                    request_id=request_id,
                    reviewer=reviewer,
                    reason=reason,
                    notify=False,
                )
        except SessionRequest.DoesNotExist:
            failed.append({"id": str(request_id), "error": "Request not found."})
        except ValidationError as exc:
            failed.append({"id": str(request_id), "error": "; ".join(exc.messages)})
        else:
            succeeded.append(str(session_request.pk))
            succeeded_requests.append(session_request)

    if succeeded_requests:
        notify_decision_summary(
            succeeded_requests,
            "approved" if action == "approve" else "rejected",
        )

    return {"succeeded": succeeded, "failed": failed}


def can_reschedule_request(user, session_request):
    if not user.is_authenticated:
        return False
    if session_request.status not in {
        SessionRequest.Status.PENDING,
        SessionRequest.Status.APPROVED,
    }:
        return False
    if user.is_superuser or user.is_officer:
        return True
    return (
        session_request.status == SessionRequest.Status.PENDING
        and session_request.requesting_unit_id == user.unit_id
    )


@transaction.atomic
def reschedule_session_request(*, request_id, actor, new_start, new_end):
    session_request = SessionRequest.objects.select_for_update().get(pk=request_id)
    if not can_reschedule_request(actor, session_request):
        raise PermissionDenied("You cannot reschedule this request.")

    new_start = _ensure_aware(new_start)
    new_end = _ensure_aware(new_end)
    place = Place.objects.select_for_update().get(pk=session_request.place_id)
    _validate_request_time(place, new_start, new_end)

    conflict = (
        SessionRequest.objects.filter(
            place=place,
            status=SessionRequest.Status.APPROVED,
            start_at__lt=new_end,
            end_at__gt=new_start,
        )
        .exclude(pk=session_request.pk)
        .exists()
    )
    if conflict:
        raise ValidationError("This place is already booked for that time.")

    old_start = session_request.start_at
    session_request.start_at = new_start
    session_request.end_at = new_end
    session_request.save(update_fields=["start_at", "end_at", "updated_at"])
    RequestStatusHistory.objects.create(
        request=session_request,
        from_status=session_request.status,
        to_status=session_request.status,
        changed_by=actor,
        reason=(
            "Rescheduled from "
            f"{timezone.localtime(old_start):%Y-%m-%d %H:%M} to "
            f"{timezone.localtime(new_start):%Y-%m-%d %H:%M}"
        ),
    )
    return session_request


def google_calendar_url(session_request, *, absolute_url=""):
    """Build a one-click Google Calendar template link for an approved session."""
    start = session_request.start_at.astimezone(UTC)
    end = session_request.end_at.astimezone(UTC)
    location = ", ".join(
        part
        for part in (
            session_request.place.name,
            session_request.place.building,
            session_request.place.room,
        )
        if part
    )
    details = session_request.purpose
    if absolute_url:
        details = f"{details}\n\n{absolute_url}"

    params = {
        "action": "TEMPLATE",
        "text": session_request.title,
        "dates": f"{start:%Y%m%dT%H%M%SZ}/{end:%Y%m%dT%H%M%SZ}",
        "details": details,
        "location": location,
        "ctz": settings.TIME_ZONE,
    }
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def _event_location(session_request):
    return ", ".join(
        part
        for part in (
            session_request.place.name,
            session_request.place.building,
            session_request.place.room,
        )
        if part
    )


def build_ics_calendar(session_requests, *, request=None):
    """Serialize approved session requests as an iCalendar document."""
    calendar = Calendar()
    calendar.add("prodid", "-//CS/CIS Beni-Suef Chapter//Session Registration//EN")
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", "CS/CIS Session Registration")
    calendar.add("x-wr-timezone", settings.TIME_ZONE)

    for session_request in session_requests:
        event = Event()
        event.add("uid", str(session_request.pk))
        event.add("dtstamp", timezone.now())
        event.add("dtstart", session_request.start_at.astimezone(UTC))
        event.add("dtend", session_request.end_at.astimezone(UTC))
        event.add("summary", session_request.title)
        event.add("location", _event_location(session_request))
        event.add("status", "CONFIRMED")
        if request is not None:
            event.add(
                "url",
                request.build_absolute_uri(
                    reverse("request_detail", args=[session_request.pk])
                ),
            )
        calendar.add_component(event)

    return calendar.to_ical()


def get_calendar_feed(user):
    feed, _ = CalendarFeed.objects.get_or_create(user=user)
    return feed


def rotate_calendar_feed(user):
    feed = get_calendar_feed(user)
    feed.token = uuid.uuid4()
    feed.is_active = True
    feed.save(update_fields=["token", "is_active"])
    return feed


@transaction.atomic
def delete_session_request(*, request_id, actor, reason):
    if not actor.is_superuser:
        raise PermissionDenied("Only admins can delete session requests.")
    if not reason.strip():
        raise ValidationError("A deletion reason is required.")

    session_request = SessionRequest.all_objects.select_for_update().get(pk=request_id)
    if session_request.deleted_at is not None:
        raise ValidationError("This request is already deleted.")

    was_approved = session_request.status == SessionRequest.Status.APPROVED
    session_request.deleted_at = timezone.now()
    session_request.deleted_by = actor
    session_request.deletion_reason = reason.strip()
    session_request.save(
        update_fields=[
            "deleted_at",
            "deleted_by",
            "deletion_reason",
            "updated_at",
        ]
    )
    RequestStatusHistory.objects.create(
        request=session_request,
        from_status=session_request.status,
        to_status=session_request.status,
        changed_by=actor,
        reason=f"Deleted by admin: {reason.strip()}",
    )
    if was_approved:
        notify_deletion(session_request)
    return session_request


@transaction.atomic
def restore_session_request(*, request_id, actor):
    if not actor.is_superuser:
        raise PermissionDenied("Only admins can restore session requests.")

    session_request = SessionRequest.all_objects.select_for_update().get(pk=request_id)
    if session_request.deleted_at is None:
        raise ValidationError("This request is not deleted.")

    if session_request.status == SessionRequest.Status.APPROVED:
        conflict = (
            SessionRequest.objects.filter(
                place=session_request.place,
                status=SessionRequest.Status.APPROVED,
                start_at__lt=session_request.end_at,
                end_at__gt=session_request.start_at,
            )
            .exclude(pk=session_request.pk)
            .exists()
        )
        if conflict:
            raise ValidationError(
                "Cannot restore: this place is now booked for that time."
            )

    status = session_request.status
    session_request.deleted_at = None
    session_request.deleted_by = None
    session_request.deletion_reason = ""
    session_request.save(
        update_fields=[
            "deleted_at",
            "deleted_by",
            "deletion_reason",
            "updated_at",
        ]
    )
    RequestStatusHistory.objects.create(
        request=session_request,
        from_status=status,
        to_status=status,
        changed_by=actor,
        reason="Restored by admin",
    )
    return session_request

