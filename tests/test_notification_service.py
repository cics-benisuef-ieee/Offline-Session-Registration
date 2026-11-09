import pytest
from django.core import mail

from bookings.services import (
    approve_session_request,
    cancel_session_request,
    create_session_request,
    delete_session_request,
    reject_session_request,
)
from notifications.models import Notification
from notifications.services import notify_reminder, notify_user

pytestmark = pytest.mark.django_db


def make_request(user, place, start_at, end_at):
    return create_session_request(
        user=user,
        place=place,
        start_at=start_at,
        end_at=end_at,
        title="Notification test",
        purpose="Check in-app notifications.",
    )


def test_notify_user_writes_in_app_without_email(board_user):
    notification = notify_user(
        user=board_user,
        kind=Notification.Kind.NEW_REQUEST,
        title="A title",
        body="A body",
    )

    assert notification.user == board_user
    assert notification.kind == Notification.Kind.NEW_REQUEST
    assert notification.read_at is None
    assert notification.is_read is False
    assert mail.outbox == []


def test_new_request_creates_officer_in_app_notification(
    board_user, officer, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)

    assert Notification.objects.filter(
        user=officer,
        kind=Notification.Kind.NEW_REQUEST,
        request=session_request,
    ).exists()
    assert mail.outbox == []


def test_decision_notifies_requester_and_officers(
    board_user, officer, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)

    reject_session_request(
        request_id=session_request.pk,
        reviewer=officer,
        reason="Not available.",
    )

    for user in (board_user, officer):
        notification = Notification.objects.get(
            user=user,
            kind=Notification.Kind.DECISION,
            request=session_request,
        )
        assert "rejected" in notification.title
    assert mail.outbox == []


def test_decision_is_deduplicated_when_requester_is_officer(
    admin_user, officer, place, start_at, end_at
):
    session_request = make_request(officer, place, start_at, end_at)

    approve_session_request(request_id=session_request.pk, reviewer=admin_user)

    assert (
        Notification.objects.filter(
            user=officer,
            kind=Notification.Kind.DECISION,
            request=session_request,
        ).count()
        == 1
    )


def test_cancellation_notifies_requester_and_officers(
    board_user, officer, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    approve_session_request(request_id=session_request.pk, reviewer=officer)

    cancel_session_request(
        request_id=session_request.pk,
        actor=board_user,
        reason="Schedule changed.",
    )

    for user in (board_user, officer):
        assert Notification.objects.filter(
            user=user,
            kind=Notification.Kind.CANCELLATION,
            request=session_request,
        ).exists()
    assert mail.outbox == []


def test_deletion_creates_in_app_notifications(
    admin_user,
    board_user,
    officer,
    place,
    start_at,
    end_at,
):
    session_request = make_request(board_user, place, start_at, end_at)
    approve_session_request(request_id=session_request.pk, reviewer=officer)

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
    assert mail.outbox == []


def test_reminder_notifications_are_distinct_per_window(
    board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)

    first = notify_reminder(session_request, hours=24)
    second = notify_reminder(session_request, hours=2)

    assert first.pk != second.pk
    assert Notification.objects.filter(
        request=session_request,
        kind__in=[Notification.Kind.REMINDER_24H, Notification.Kind.REMINDER_2H],
    ).count() == 2
    assert mail.outbox == []


def test_repeated_reminder_is_deduplicated(
    board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)

    first = notify_reminder(session_request, hours=24)
    second = notify_reminder(session_request, hours=24)

    assert first.pk == second.pk
    assert mail.outbox == []
    assert Notification.objects.filter(
        request=session_request,
        kind=Notification.Kind.REMINDER_24H,
    ).count() == 1
