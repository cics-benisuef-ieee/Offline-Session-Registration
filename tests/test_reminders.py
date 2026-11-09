from datetime import datetime, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from bookings.models import SessionRequest
from bookings.services import (
    approve_session_request,
    create_session_request,
    delete_session_request,
    reject_session_request,
)
from notifications.digest import build_digest_querysets, send_officer_digest
from notifications.models import Notification, NotificationPreference
from notifications.reminders import send_due_reminders

pytestmark = pytest.mark.django_db


def make_request(user, place, start_at, end_at, title="Reminder test"):
    return create_session_request(
        user=user,
        place=place,
        start_at=start_at,
        end_at=end_at,
        title=title,
        purpose="Exercise reminders and digests.",
    )


def approved_request(officer, user, place, start_at, end_at, title="Approved"):
    session_request = make_request(user, place, start_at, end_at, title=title)
    approve_session_request(request_id=session_request.pk, reviewer=officer)
    return session_request


def move_to(session_request, *, now, hours):
    SessionRequest.objects.filter(pk=session_request.pk).update(
        start_at=now + timedelta(hours=hours),
        end_at=now + timedelta(hours=hours, minutes=60),
    )
    session_request.refresh_from_db()
    return session_request


def fixed_now():
    return timezone.now().replace(second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------


def test_24h_reminder_is_sent_once(
    django_capture_on_commit_callbacks,
    officer,
    board_user,
    place,
    start_at,
    end_at,
):
    now = fixed_now()
    session_request = approved_request(officer, board_user, place, start_at, end_at)
    move_to(session_request, now=now, hours=12)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        first = send_due_reminders(now=now)

    assert first == {"sent": 1, "skipped": 0}
    assert mail.outbox == []

    session_request.refresh_from_db()
    assert session_request.reminder_24h_sent_at is not None
    assert session_request.reminder_2h_sent_at is None
    assert (
        Notification.objects.filter(
            request=session_request,
            kind=Notification.Kind.REMINDER_24H,
        ).count()
        == 1
    )

    with django_capture_on_commit_callbacks(execute=True):
        second = send_due_reminders(now=now)

    assert second == {"sent": 0, "skipped": 0}
    assert mail.outbox == []
    assert (
        Notification.objects.filter(
            request=session_request,
            kind=Notification.Kind.REMINDER_24H,
        ).count()
        == 1
    )


def test_2h_reminder_fires_independently_of_the_24h_one(
    django_capture_on_commit_callbacks,
    officer,
    board_user,
    place,
    start_at,
    end_at,
):
    now = fixed_now()
    session_request = approved_request(officer, board_user, place, start_at, end_at)
    move_to(session_request, now=now, hours=12)

    with django_capture_on_commit_callbacks(execute=True):
        send_due_reminders(now=now)
    session_request.refresh_from_db()
    assert session_request.reminder_24h_sent_at is not None
    assert session_request.reminder_2h_sent_at is None

    two_hours_out = now + timedelta(hours=10)
    with django_capture_on_commit_callbacks(execute=True):
        result = send_due_reminders(now=two_hours_out)

    assert result == {"sent": 1, "skipped": 0}
    assert mail.outbox == []

    session_request.refresh_from_db()
    assert session_request.reminder_2h_sent_at is not None
    assert (
        Notification.objects.filter(
            request=session_request,
            kind=Notification.Kind.REMINDER_2H,
        ).count()
        == 1
    )


def test_reminder_creates_in_app_notification(
    django_capture_on_commit_callbacks,
    officer,
    board_user,
    place,
    start_at,
    end_at,
):
    now = fixed_now()
    session_request = approved_request(officer, board_user, place, start_at, end_at)
    move_to(session_request, now=now, hours=12)

    with django_capture_on_commit_callbacks(execute=True):
        result = send_due_reminders(now=now)

    assert result == {"sent": 1, "skipped": 0}
    assert mail.outbox == []
    assert Notification.objects.filter(
        request=session_request,
        kind=Notification.Kind.REMINDER_24H,
        user=board_user,
    ).exists()
    session_request.refresh_from_db()
    assert session_request.reminder_24h_sent_at is not None


def test_only_approved_upcoming_sessions_receive_reminders(
    django_capture_on_commit_callbacks,
    admin_user,
    officer,
    board_user,
    place,
    start_at,
    end_at,
):
    now = fixed_now()

    pending = make_request(board_user, place, start_at, end_at, title="Pending")
    move_to(pending, now=now, hours=12)

    rejected = make_request(board_user, place, start_at, end_at, title="Rejected")
    move_to(rejected, now=now, hours=12)
    reject_session_request(
        request_id=rejected.pk,
        reviewer=officer,
        reason="Not available.",
    )

    deleted = approved_request(officer, board_user, place, start_at, end_at, "Deleted")
    move_to(deleted, now=now, hours=12)
    delete_session_request(
        request_id=deleted.pk,
        actor=admin_user,
        reason="Test row.",
    )

    started = approved_request(officer, board_user, place, start_at, end_at, "Started")
    move_to(started, now=now, hours=-1)

    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        result = send_due_reminders(now=now)

    assert result == {"sent": 0, "skipped": 0}
    assert mail.outbox == []
    assert not Notification.objects.filter(
        kind__in=[Notification.Kind.REMINDER_24H, Notification.Kind.REMINDER_2H]
    ).exists()


def test_send_session_reminders_command_reports_counts(
    django_capture_on_commit_callbacks,
    officer,
    board_user,
    place,
    start_at,
    end_at,
):
    session_request = approved_request(officer, board_user, place, start_at, end_at)
    move_to(session_request, now=timezone.now(), hours=12)
    stdout = StringIO()

    with django_capture_on_commit_callbacks(execute=True):
        call_command("send_session_reminders", stdout=stdout)

    assert "Reminders sent: 1" in stdout.getvalue()
    session_request.refresh_from_db()
    assert session_request.reminder_24h_sent_at is not None


# ---------------------------------------------------------------------------
# Officer digest
# ---------------------------------------------------------------------------


def test_digest_querysets_only_include_pending_and_upcoming(
    officer, board_user, place, start_at, end_at
):
    now = fixed_now()

    pending = make_request(board_user, place, start_at, end_at, title="Pending")
    upcoming = approved_request(officer, board_user, place, start_at, end_at, "Upcoming")
    move_to(upcoming, now=now, hours=24)

    far_future = approved_request(officer, board_user, place, start_at, end_at, "Far")
    move_to(far_future, now=now, hours=24 * 20)

    rejected = make_request(board_user, place, start_at, end_at, title="Rejected")
    reject_session_request(request_id=rejected.pk, reviewer=officer, reason="No.")

    pending_list, upcoming_list = build_digest_querysets(now=now)

    assert [item.pk for item in pending_list] == [pending.pk]
    assert [item.pk for item in upcoming_list] == [upcoming.pk]


def test_digest_sends_to_daily_officers(
    django_capture_on_commit_callbacks, officer, board_user, place, start_at, end_at
):
    make_request(board_user, place, start_at, end_at, title="Pending digest item")

    with django_capture_on_commit_callbacks(execute=True):
        result = send_officer_digest(now=fixed_now())

    assert result == {"sent": 1, "skipped": 0}
    assert mail.outbox == []
    assert Notification.objects.filter(
        user=officer, kind=Notification.Kind.DIGEST
    ).exists()


def test_digest_respects_off_frequency(
    django_capture_on_commit_callbacks, officer
):
    preference = NotificationPreference.for_user(officer)
    preference.digest_frequency = NotificationPreference.DigestFrequency.OFF
    preference.save(update_fields=["digest_frequency"])
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        result = send_officer_digest(now=fixed_now())

    assert result == {"sent": 0, "skipped": 1}
    assert mail.outbox == []
    assert not Notification.objects.filter(kind=Notification.Kind.DIGEST).exists()


def test_weekly_digest_only_sends_on_monday(
    django_capture_on_commit_callbacks, officer
):
    preference = NotificationPreference.for_user(officer)
    preference.digest_frequency = NotificationPreference.DigestFrequency.WEEKLY
    preference.save(update_fields=["digest_frequency"])

    tz = ZoneInfo(settings.TIME_ZONE)
    base = datetime.now(tz=tz).replace(hour=12, minute=0, second=0, microsecond=0)
    monday = base - timedelta(days=base.weekday())
    tuesday = monday + timedelta(days=1)

    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        tuesday_result = send_officer_digest(now=tuesday)
    assert tuesday_result == {"sent": 0, "skipped": 1}
    assert mail.outbox == []

    with django_capture_on_commit_callbacks(execute=True):
        monday_result = send_officer_digest(now=monday)
    assert monday_result == {"sent": 1, "skipped": 0}
    assert mail.outbox == []


def test_send_officer_digest_command_reports_counts(
    django_capture_on_commit_callbacks, officer
):
    stdout = StringIO()

    with django_capture_on_commit_callbacks(execute=True):
        call_command("send_officer_digest", stdout=stdout)

    assert "Digests sent: 1" in stdout.getvalue()


# ---------------------------------------------------------------------------
# Cron endpoints
# ---------------------------------------------------------------------------


def test_cron_endpoints_reject_missing_or_wrong_secret(client, settings):
    settings.CRON_SECRET = ""
    assert client.post(reverse("cron_reminders")).status_code == 401
    assert client.get(reverse("cron_digest")).status_code == 401

    settings.CRON_SECRET = "test-cron-secret"
    assert client.post(reverse("cron_reminders")).status_code == 401
    assert (
        client.post(
            reverse("cron_reminders"),
            HTTP_AUTHORIZATION="Bearer wrong-secret",
        ).status_code
        == 401
    )
    assert client.get(reverse("cron_digest")).status_code == 401
    assert (
        client.get(
            reverse("cron_digest"),
            HTTP_AUTHORIZATION="Bearer wrong-secret",
        ).status_code
        == 401
    )


def test_cron_endpoints_accept_the_configured_secret(client, settings):
    settings.CRON_SECRET = "test-cron-secret"

    reminders = client.post(
        reverse("cron_reminders"),
        HTTP_AUTHORIZATION="Bearer test-cron-secret",
    )
    assert reminders.status_code == 200
    assert reminders.json() == {"sent": 0, "skipped": 0}

    digest = client.get(
        reverse("cron_digest"),
        HTTP_AUTHORIZATION="Bearer test-cron-secret",
    )
    assert digest.status_code == 200
    assert digest.json() == {"sent": 0, "skipped": 0}


def test_cron_reminders_requires_post(client, settings):
    settings.CRON_SECRET = "test-cron-secret"

    response = client.get(
        reverse("cron_reminders"),
        HTTP_AUTHORIZATION="Bearer test-cron-secret",
    )

    assert response.status_code == 405
