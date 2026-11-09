import pytest
from django.core import mail

from bookings.services import (
    approve_session_request,
    cancel_session_request,
    create_session_request,
    reject_session_request,
)
from notifications.models import Notification

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


def test_new_request_notifies_officers_without_email(
    board_user, officer, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)

    assert Notification.objects.filter(
        user=officer,
        kind=Notification.Kind.NEW_REQUEST,
        request=session_request,
    ).exists()
    assert mail.outbox == []


def test_decision_notifies_requester_and_officers_without_email(
    board_user, officer, place, start_at, end_at
):
    request = make_request(board_user, place, start_at, end_at)

    reject_session_request(
        request_id=request.pk,
        reviewer=officer,
        reason="Not available",
    )

    for user in (board_user, officer):
        assert Notification.objects.filter(
            user=user,
            kind=Notification.Kind.DECISION,
            request=request,
        ).exists()
    assert mail.outbox == []


def test_cancellation_notifies_requester_and_officers_without_email(
    board_user, officer, place, start_at, end_at
):
    request = make_request(board_user, place, start_at, end_at)
    approve_session_request(request_id=request.pk, reviewer=officer)

    cancel_session_request(
        request_id=request.pk,
        actor=board_user,
        reason="Schedule changed",
    )

    for user in (board_user, officer):
        assert Notification.objects.filter(
            user=user,
            kind=Notification.Kind.CANCELLATION,
            request=request,
        ).exists()
    assert mail.outbox == []
