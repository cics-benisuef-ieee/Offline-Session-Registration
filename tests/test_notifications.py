import pytest
from django.core import mail

from bookings.services import (
    approve_session_request,
    cancel_session_request,
    create_session_request,
    reject_session_request,
)

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


def test_new_request_notifies_officers(
    django_capture_on_commit_callbacks, board_user, officer, place, start_at, end_at
):
    with django_capture_on_commit_callbacks(execute=True):
        make_request(board_user, place, start_at, end_at)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [officer.email]


def test_decision_notifies_requester(
    django_capture_on_commit_callbacks, board_user, officer, place, start_at, end_at
):
    with django_capture_on_commit_callbacks(execute=True):
        request = make_request(board_user, place, start_at, end_at)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        reject_session_request(
            request_id=request.pk,
            reviewer=officer,
            reason="Not available",
        )

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [board_user.email]


def test_cancellation_notifies_officers(
    django_capture_on_commit_callbacks, board_user, officer, place, start_at, end_at
):
    with django_capture_on_commit_callbacks(execute=True):
        request = make_request(board_user, place, start_at, end_at)
        approve_session_request(request_id=request.pk, reviewer=officer)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        cancel_session_request(
            request_id=request.pk,
            actor=board_user,
            reason="Schedule changed",
        )

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [officer.email]
