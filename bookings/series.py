import calendar
from datetime import date, datetime, time, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from notifications.services import (
    notify_cancellation_summary,
    notify_new_series,
)

from .models import BookingSeries, RequestStatusHistory, SessionRequest
from .services import (
    MAX_DURATION_MINUTES,
    MIN_DURATION_MINUTES,
    SLOT_MINUTES,
    _derive_units,
    _ensure_aware,
    _local_tz,
    _validate_request_time,
    bulk_decide,
    cancel_session_request,
    reschedule_session_request,
)

MAX_SERIES_OCCURRENCES = 52
WEEKDAY_CHOICES = [
    (0, "Monday"),
    (1, "Tuesday"),
    (2, "Wednesday"),
    (3, "Thursday"),
    (4, "Friday"),
    (5, "Saturday"),
    (6, "Sunday"),
]


def _weekly_dates(start_date, until_date, weekday, step_days):
    offset = (int(weekday) - start_date.weekday()) % 7
    current = start_date + timedelta(days=offset)
    while current <= until_date:
        yield current
        current += timedelta(days=step_days)


def _monthly_dates(start_date, until_date):
    """Yield the same day each month, clamped to the month's last day."""
    year, month, anchor_day = start_date.year, start_date.month, start_date.day
    while True:
        last_day = calendar.monthrange(year, month)[1]
        current = date(year, month, min(anchor_day, last_day))
        if current > until_date:
            break
        if current >= start_date:
            yield current
        month += 1
        if month > 12:
            month = 1
            year += 1


def _series_dates(
    *, frequency, weekday, start_date, until_date, occurrence_count
):
    if until_date is None:
        until_date = date.max
    if until_date < start_date:
        raise ValidationError("The series end date must be on or after the start date.")

    if frequency == BookingSeries.Frequency.WEEKLY:
        if weekday is None:
            raise ValidationError("Choose a weekday for a weekly series.")
        dates = _weekly_dates(start_date, until_date, weekday, 7)
    elif frequency == BookingSeries.Frequency.BIWEEKLY:
        if weekday is None:
            raise ValidationError("Choose a weekday for a biweekly series.")
        dates = _weekly_dates(start_date, until_date, weekday, 14)
    elif frequency == BookingSeries.Frequency.MONTHLY:
        dates = _monthly_dates(start_date, until_date)
    else:
        raise ValidationError("Choose a valid repeat frequency.")

    results = []
    for current in dates:
        results.append(current)
        if occurrence_count and len(results) >= occurrence_count:
            break
        if len(results) >= MAX_SERIES_OCCURRENCES:
            break
    return results


def build_series_occurrences(
    *,
    place,
    frequency,
    weekday,
    start_time,
    duration_minutes,
    start_date,
    until_date=None,
    occurrence_count=None,
):
    """Return candidate occurrences with validation and conflict flags.

    Nothing is written to the database. Invalid occurrences keep a ``reason``
    so the preview can explain why they cannot be selected.
    """
    duration_minutes = int(duration_minutes)
    if duration_minutes < MIN_DURATION_MINUTES or duration_minutes > MAX_DURATION_MINUTES:
        raise ValidationError(
            f"Duration must be between {MIN_DURATION_MINUTES} and "
            f"{MAX_DURATION_MINUTES} minutes."
        )
    if duration_minutes % SLOT_MINUTES:
        raise ValidationError("Duration must be in 30-minute increments.")
    if start_time.minute % SLOT_MINUTES or start_time.second:
        raise ValidationError("Start time must align to a 30-minute slot.")
    if weekday is not None and not 0 <= int(weekday) <= 6:
        raise ValidationError("Choose a valid weekday.")

    dates = _series_dates(
        frequency=frequency,
        weekday=weekday,
        start_date=start_date,
        until_date=until_date,
        occurrence_count=occurrence_count,
    )
    if not dates:
        raise ValidationError("This series has no occurrences in the selected range.")

    tz = _local_tz()
    occurrences = []
    for on_date in dates:
        start_at = datetime.combine(on_date, start_time, tzinfo=tz)
        end_at = start_at + timedelta(minutes=duration_minutes)
        reason = ""
        is_valid = True
        try:
            _validate_request_time(place, start_at, end_at)
        except ValidationError as exc:
            is_valid = False
            reason = "; ".join(exc.messages)

        conflict = False
        if is_valid:
            conflict = (
                SessionRequest.objects.filter(
                    place=place,
                    status=SessionRequest.Status.APPROVED,
                    start_at__lt=end_at,
                    end_at__gt=start_at,
                ).exists()
            )
            if conflict:
                reason = "This place is already booked for that time."

        occurrences.append(
            {
                "date": on_date,
                "start_at": start_at,
                "end_at": end_at,
                "is_valid": is_valid,
                "conflict": conflict,
                "reason": reason,
            }
        )
    return occurrences


def visible_series_for(user):
    if not user.is_authenticated:
        return BookingSeries.objects.none()
    if user.is_superuser or user.is_officer:
        return BookingSeries.objects.all()
    if not user.unit_id:
        return BookingSeries.objects.none()
    return BookingSeries.objects.filter(requesting_unit=user.unit)


def can_manage_series(user, series):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_officer:
        return True
    return series.requesting_unit_id == user.unit_id


@transaction.atomic
def create_booking_series(
    *,
    user,
    place,
    title,
    purpose,
    frequency,
    weekday,
    start_time,
    duration_minutes,
    start_date,
    until_date=None,
    occurrence_count=None,
    selected_dates,
    requesting_unit=None,
):
    unit, reviewer_unit = _derive_units(user, requesting_unit)

    if not title.strip():
        raise ValidationError("A title is required.")
    if not purpose.strip():
        raise ValidationError("A purpose is required.")

    occurrences = build_series_occurrences(
        place=place,
        frequency=frequency,
        weekday=weekday,
        start_time=start_time,
        duration_minutes=duration_minutes,
        start_date=start_date,
        until_date=until_date,
        occurrence_count=occurrence_count,
    )
    by_date = {occurrence["date"]: occurrence for occurrence in occurrences}

    selected = []
    for raw_date in selected_dates:
        parsed = parse_date(raw_date) if isinstance(raw_date, str) else raw_date
        if parsed is None:
            raise ValidationError("Choose valid occurrence dates.")
        occurrence = by_date.get(parsed)
        if occurrence is None:
            raise ValidationError("That occurrence is not part of this series.")
        if not occurrence["is_valid"]:
            raise ValidationError(
                f"{parsed:%b %d} cannot be booked: {occurrence['reason']}"
            )
        if occurrence["conflict"]:
            raise ValidationError(
                f"{parsed:%b %d} conflicts with an approved booking."
            )
        selected.append(occurrence)

    if not selected:
        raise ValidationError("Select at least one occurrence.")

    selected.sort(key=lambda occurrence: occurrence["start_at"])

    series = BookingSeries.objects.create(
        requesting_unit=unit,
        submitted_by=user,
        place=place,
        title=title.strip(),
        purpose=purpose.strip(),
        frequency=frequency,
        weekday=weekday,
        start_time=start_time,
        duration_minutes=int(duration_minutes),
        start_date=start_date,
        until_date=selected[-1]["date"],
    )

    for index, occurrence in enumerate(selected):
        session_request = SessionRequest.objects.create(
            requesting_unit=unit,
            reviewer_unit=reviewer_unit,
            submitted_by=user,
            place=place,
            series=series,
            series_index=index,
            title=series.title,
            purpose=series.purpose,
            start_at=occurrence["start_at"],
            end_at=occurrence["end_at"],
            status=SessionRequest.Status.PENDING,
        )
        RequestStatusHistory.objects.create(
            request=session_request,
            from_status="",
            to_status=SessionRequest.Status.PENDING,
            changed_by=user,
            reason="Request created — series",
        )

    notify_new_series(series)
    return series


def approve_series(*, series_id, reviewer):
    if not (reviewer.is_superuser or reviewer.is_officer):
        raise PermissionDenied("Only officers can approve requests.")
    series = BookingSeries.objects.get(pk=series_id)
    request_ids = list(
        series.occurrences.filter(status=SessionRequest.Status.PENDING).values_list(
            "pk", flat=True
        )
    )
    return bulk_decide(
        request_ids=request_ids,
        action="approve",
        reviewer=reviewer,
    )


def reject_series(*, series_id, reviewer, reason):
    if not (reviewer.is_superuser or reviewer.is_officer):
        raise PermissionDenied("Only officers can reject requests.")
    if not reason.strip():
        raise ValidationError("A rejection reason is required.")
    series = BookingSeries.objects.get(pk=series_id)
    request_ids = list(
        series.occurrences.filter(status=SessionRequest.Status.PENDING).values_list(
            "pk", flat=True
        )
    )
    return bulk_decide(
        request_ids=request_ids,
        action="reject",
        reviewer=reviewer,
        reason=reason,
    )


def _occurrence_from(series, occurrence):
    if isinstance(occurrence, SessionRequest):
        return occurrence
    return series.occurrences.get(pk=occurrence)


@transaction.atomic
def cancel_series(
    *,
    series_id,
    actor,
    reason="",
    scope="future",
    from_occurrence=None,
):
    series = BookingSeries.objects.get(pk=series_id)
    if not can_manage_series(actor, series):
        raise PermissionDenied("You cannot cancel this series.")
    if scope not in {"this", "future"}:
        raise ValidationError("Choose whether to cancel this occurrence or the future.")

    occurrences = series.occurrences.filter(
        status__in=[SessionRequest.Status.PENDING, SessionRequest.Status.APPROVED]
    )
    if from_occurrence is not None:
        anchor = _occurrence_from(series, from_occurrence)
        if scope == "this":
            occurrences = occurrences.filter(pk=anchor.pk)
        else:
            occurrences = occurrences.filter(
                start_at__gte=max(anchor.start_at, timezone.now()),
            )
    else:
        occurrences = occurrences.filter(start_at__gte=timezone.now())

    if (
        occurrences.filter(status=SessionRequest.Status.APPROVED).exists()
        and not reason.strip()
    ):
        raise ValidationError(
            "A cancellation reason is required for approved occurrences."
        )

    succeeded = []
    cancelled_requests = []
    failed = []
    for session_request in occurrences.select_for_update().order_by("start_at"):
        try:
            cancelled = cancel_session_request(
                request_id=session_request.pk,
                actor=actor,
                reason=reason,
                notify=False,
            )
        except (PermissionDenied, ValidationError) as exc:
            failed.append(
                {
                    "id": str(session_request.pk),
                    "error": "; ".join(getattr(exc, "messages", [str(exc)])),
                }
            )
        else:
            succeeded.append(str(session_request.pk))
            cancelled_requests.append(cancelled)

    if cancelled_requests:
        notify_cancellation_summary(cancelled_requests, reason=reason.strip())

    return {"succeeded": succeeded, "failed": failed}


@transaction.atomic
def reschedule_series_future(
    *,
    series_id,
    actor,
    from_occurrence,
    new_start_time=None,
    new_weekday=None,
):
    series = BookingSeries.objects.get(pk=series_id)
    if not can_manage_series(actor, series):
        raise PermissionDenied("You cannot reschedule this series.")
    if new_start_time is None and new_weekday is None:
        raise ValidationError("Choose a new time or weekday.")

    anchor = _occurrence_from(series, from_occurrence)
    occurrences = (
        series.occurrences.filter(
            status=SessionRequest.Status.PENDING,
            start_at__gte=max(anchor.start_at, timezone.now()),
        )
        .select_for_update()
        .order_by("start_at")
    )

    succeeded = []
    failed = []
    for session_request in occurrences:
        local_start = timezone.localtime(session_request.start_at)
        new_date = local_start.date()
        if new_weekday is not None:
            new_date = new_date + timedelta(
                days=(int(new_weekday) - new_date.weekday())
            )
        new_time = new_start_time or local_start.time()
        new_start = datetime.combine(new_date, new_time, tzinfo=_local_tz())
        new_end = new_start + timedelta(minutes=session_request.duration_minutes)
        try:
            reschedule_session_request(
                request_id=session_request.pk,
                actor=actor,
                new_start=_ensure_aware(new_start),
                new_end=_ensure_aware(new_end),
            )
        except (PermissionDenied, ValidationError) as exc:
            failed.append(
                {
                    "id": str(session_request.pk),
                    "error": "; ".join(getattr(exc, "messages", [str(exc)])),
                }
            )
        else:
            succeeded.append(str(session_request.pk))

    return {"succeeded": succeeded, "failed": failed}


def parse_series_time(raw_value):
    """Parse an ``HH:MM`` string into a ``time`` for form handling."""
    if not raw_value:
        return None
    try:
        hour, minute = (int(part) for part in raw_value.split(":", 1))
    except (TypeError, ValueError):
        raise ValidationError("Enter a valid time.") from None
    return time(hour=hour, minute=minute)
