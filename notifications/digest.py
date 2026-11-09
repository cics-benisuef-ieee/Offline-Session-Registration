from datetime import timedelta

from django.utils import timezone

from bookings.models import SessionRequest

from .models import NotificationPreference
from .services import _officers, notify_digest


def build_digest_querysets(now=None):
    now = now or timezone.now()
    pending = list(
        SessionRequest.objects.filter(
            status=SessionRequest.Status.PENDING,
        )
        .select_related("requesting_unit", "place")
        .order_by("start_at")
    )
    upcoming = list(
        SessionRequest.objects.filter(
            status=SessionRequest.Status.APPROVED,
            start_at__gte=now,
            start_at__lte=now + timedelta(days=14),
        )
        .select_related("requesting_unit", "place")
        .order_by("start_at")
    )
    return pending, upcoming


def send_officer_digest(now=None):
    """Send the pending/upcoming digest to officers at their chosen cadence."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    pending, upcoming = build_digest_querysets(now=now)
    sent = 0
    skipped = 0

    for officer in _officers():
        preference = NotificationPreference.for_user(officer)
        frequency = preference.digest_frequency
        if frequency == NotificationPreference.DigestFrequency.OFF:
            skipped += 1
            continue
        if (
            frequency == NotificationPreference.DigestFrequency.WEEKLY
            and today.weekday() != 0
        ):
            skipped += 1
            continue

        notify_digest(
            officer,
            pending=pending,
            upcoming=upcoming,
            frequency=frequency,
        )
        sent += 1

    return {"sent": sent, "skipped": skipped}
