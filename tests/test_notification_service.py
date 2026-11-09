import pytest
from django.core import mail

from bookings.services import (
    approve_session_request,
    cancel_session_request,
    create_session_request,
    delete_session_request,
    reject_session_request,
)
from notifications.models import Notification, NotificationPreference
from notifications.services import notify_reminder, notify_user

pytestmark = pytest.mark.django_db


def make_request(user, place, start_at, end_at):
    return create_session_request(
        user=user,
        place=place,
        start_at=start_at,
        end_at=end_at,
        title="Notification test",
        purpose="Check transactional email.",
    )


def test_notify_user_writes_in_app_and_email(
    django_capture_on_commit_callbacks, board_user
):
    with django_capture_on_commit_callbacks(execute=True):
        notification = notify_user(
            user=board_user,
            kind=Notification.Kind.NEW_REQUEST,
            title="A title",
            body="A body",
            email_subject="Email subject",
            email_text="Email body",
            email_html="<p>Email body</p>",
        )

    assert notification.user == board_user
    assert notification.kind == Notification.Kind.NEW_REQUEST
    assert notification.read_at is None
    assert notification.is_read is False
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [board_user.email]
    assert mail.outbox[0].subject == "Email subject"
    assert mail.outbox[0].body == "Email body"


def test_email_preference_suppresses_email_but_not_in_app_row(
    django_capture_on_commit_callbacks, board_user
):
    preference = NotificationPreference.for_user(board_user)
    preference.email_new_request = False
    preference.save(update_fields=["email_new_request"])

    with django_capture_on_commit_callbacks(execute=True):
        notify_user(
            user=board_user,
            kind=Notification.Kind.NEW_REQUEST,
            title="In-app only",
            body="Body",
            email_subject="Should not send",
            email_text="Should not send",
        )

    assert Notification.objects.filter(
        user=board_user, kind=Notification.Kind.NEW_REQUEST
    ).exists()
    assert mail.outbox == []


def test_notification_preference_is_created_once(board_user):
    first = NotificationPreference.for_user(board_user)
    second = NotificationPreference.for_user(board_user)

    assert first.pk == second.pk
    assert NotificationPreference.objects.filter(user=board_user).count() == 1


def test_new_request_creates_officer_in_app_notification(
    django_capture_on_commit_callbacks, board_user, officer, place, start_at, end_at
):
    with django_capture_on_commit_callbacks(execute=True):
        session_request = make_request(board_user, place, start_at, end_at)

    assert Notification.objects.filter(
        user=officer,
        kind=Notification.Kind.NEW_REQUEST,
        request=session_request,
    ).exists()


def test_decision_creates_requester_in_app_notification(
    django_capture_on_commit_callbacks, board_user, officer, place, start_at, end_at
):
    with django_capture_on_commit_callbacks(execute=True):
        session_request = make_request(board_user, place, start_at, end_at)

    with django_capture_on_commit_callbacks(execute=True):
        reject_session_request(
            request_id=session_request.pk,
            reviewer=officer,
            reason="Not available.",
        )

    notification = Notification.objects.get(
        user=board_user,
        kind=Notification.Kind.DECISION,
        request=session_request,
    )
    assert "rejected" in notification.title


def test_cancellation_creates_officer_in_app_notification(
    django_capture_on_commit_callbacks, board_user, officer, place, start_at, end_at
):
    with django_capture_on_commit_callbacks(execute=True):
        session_request = make_request(board_user, place, start_at, end_at)
        approve_session_request(request_id=session_request.pk, reviewer=officer)

    with django_capture_on_commit_callbacks(execute=True):
        cancel_session_request(
            request_id=session_request.pk,
            actor=board_user,
            reason="Schedule changed.",
        )

    assert Notification.objects.filter(
        user=officer,
        kind=Notification.Kind.CANCELLATION,
        request=session_request,
    ).exists()


def test_deletion_creates_in_app_notifications(
    django_capture_on_commit_callbacks,
    admin_user,
    board_user,
    officer,
    place,
    start_at,
    end_at,
):
    with django_capture_on_commit_callbacks(execute=True):
        session_request = make_request(board_user, place, start_at, end_at)
        approve_session_request(request_id=session_request.pk, reviewer=officer)

    with django_capture_on_commit_callbacks(execute=True):
        delete_session_request(
            request_id=session_request.pk,
            actor=admin_user,
            reason="Venue unavailable.",
        )

    users = set(
        Notification.objects.filter(
            kind=Notification.Kind.DELETION,
            request=session_request,
        ).values_list("user_id", flat=True)
    )
    assert board_user.pk in users
    assert officer.pk in users


def test_reminder_notifications_are_distinct_per_window(
    django_capture_on_commit_callbacks, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)

    with django_capture_on_commit_callbacks(execute=True):
        first = notify_reminder(session_request, hours=24)
    with django_capture_on_commit_callbacks(execute=True):
        second = notify_reminder(session_request, hours=2)

    assert first.pk != second.pk
    assert Notification.objects.filter(
        request=session_request,
        kind__in=[Notification.Kind.REMINDER_24H, Notification.Kind.REMINDER_2H],
    ).count() == 2


def test_repeated_reminder_is_deduplicated(
    django_capture_on_commit_callbacks, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)

    with django_capture_on_commit_callbacks(execute=True):
        first = notify_reminder(session_request, hours=24)
    assert len(mail.outbox) == 1
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        second = notify_reminder(session_request, hours=24)

    assert first.pk == second.pk
    assert mail.outbox == []
    assert Notification.objects.filter(
        request=session_request,
        kind=Notification.Kind.REMINDER_24H,
    ).count() == 1
