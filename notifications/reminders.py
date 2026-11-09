from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from bookings.models import SessionRequest

from .services import notify_reminder

# (hours before start, idempotency field on SessionRequest)
REMINDER_WINDOWS = (
    (24, "reminder_24h_sent_at"),
    (2, "reminder_2h_sent_at"),
)


def _send_reminder(request_id, *, hours, field, now):
    with transaction.atomic():
        session_request = (
            SessionRequest.objects.select_for_update()
            .filter(
                pk=request_id,
                status=SessionRequest.Status.APPROVED,
                start_at__gt=now,
            )
            .first()
        )
        if session_request is None:
            return False
        if getattr(session_request, field) is not None:
            return False

        notify_reminder(session_request, hours=hours)
        setattr(session_request, field, timezone.now())
        session_request.save(update_fields=[field])
        return True


def send_due_reminders(now=None):
    """Send any reminder that has come due since the last successful run."""
    now = now or timezone.now()
    sent = 0
    skipped = 0

    for hours, field in REMINDER_WINDOWS:
        window_end = now + timedelta(hours=hours)
        candidates = list(
            SessionRequest.objects.filter(
                status=SessionRequest.Status.APPROVED,
                start_at__gt=now,
                start_at__lte=window_end,
                **{f"{field}__isnull": True},
            ).values_list("pk", flat=True)
        )
        for request_id in candidates:
            if _send_reminder(request_id, hours=hours, field=field, now=now):
                sent += 1
            else:
                skipped += 1

    return {"sent": sent, "skipped": skipped}
