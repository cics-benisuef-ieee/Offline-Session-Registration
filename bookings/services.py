from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from notifications.services import (
    notify_cancellation,
    notify_decision,
    notify_new_request,
)
from places.models import Place, PlaceClosure
from units.models import Unit

from .models import RequestStatusHistory, SessionRequest

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
def approve_session_request(*, request_id, reviewer):
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
    notify_decision(session_request)
    return session_request


@transaction.atomic
def reject_session_request(*, request_id, reviewer, reason):
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
    notify_decision(session_request)
    return session_request


@transaction.atomic
def cancel_session_request(*, request_id, actor, reason=""):
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
    notify_cancellation(session_request)
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
