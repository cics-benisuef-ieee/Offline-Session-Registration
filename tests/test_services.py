from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from accounts.models import User
from bookings.models import RequestStatusHistory, SessionRequest
from bookings.services import (
    approve_session_request,
    cancel_session_request,
    create_session_request,
    get_available_slots,
    reject_session_request,
    visible_requests_for,
)
from places.models import PlaceClosure
from units.models import Unit

pytestmark = pytest.mark.django_db


def make_request(user, place, start_at, end_at, title="Study session"):
    return create_session_request(
        user=user,
        place=place,
        start_at=start_at,
        end_at=end_at,
        title=title,
        purpose="Prepare for the next chapter activity.",
    )


def slot_at(slots, hhmm):
    return next(slot for slot in slots if slot["start"][11:16] == hhmm)


def test_board_request_is_pending_and_visible_only_to_own_board(
    board_user, other_board_user, officer, place, start_at, end_at
):
    request = make_request(board_user, place, start_at, end_at)

    assert request.status == SessionRequest.Status.PENDING
    assert request.reviewer_unit == board_user.unit.parent
    assert list(visible_requests_for(board_user)) == [request]
    assert list(visible_requests_for(other_board_user)) == []
    assert list(visible_requests_for(officer)) == [request]


def test_branch_board_request_uses_branch_as_reviewer_unit(
    db, place, start_at, end_at
):
    branch = Unit.objects.create(
        name="Beni-Suef Student Branch",
        slug="branch",
        kind=Unit.Kind.BRANCH,
    )
    board = Unit.objects.create(
        name="Human Resources",
        slug="human-resources",
        kind=Unit.Kind.BOARD,
        parent=branch,
    )
    user = User.objects.create_user(
        email="hr@benisuef.ieee",
        password="test-password",
        full_name="Human Resources Board Account",
        unit=board,
        account_type=User.AccountType.BOARD_ACCOUNT,
    )

    request = make_request(user, place, start_at, end_at)

    assert request.reviewer_unit == branch
    assert list(visible_requests_for(user)) == [request]


def test_board_account_cannot_request_for_another_unit(
    board_user, other_board, place, start_at, end_at
):
    with pytest.raises(PermissionDenied):
        create_session_request(
            user=board_user,
            requesting_unit=other_board,
            place=place,
            start_at=start_at,
            end_at=end_at,
            title="Wrong unit",
            purpose="Should not be allowed.",
        )


def test_one_board_account_per_board(board_user, board):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            User.objects.create_user(
                email="second-board@example.com",
                password="test-password",
                full_name="Second Board Account",
                unit=board,
                account_type=User.AccountType.BOARD_ACCOUNT,
            )


def test_pending_does_not_block_but_approved_does(
    board_user, officer, place, tomorrow, start_at, end_at
):
    request = make_request(board_user, place, start_at, end_at)

    assert slot_at(get_available_slots(place, tomorrow), "10:00")["available"] is True

    approve_session_request(request_id=request.pk, reviewer=officer)

    assert slot_at(get_available_slots(place, tomorrow), "10:00")["available"] is False
    assert slot_at(get_available_slots(place, tomorrow), "10:30")["available"] is False
    assert slot_at(get_available_slots(place, tomorrow), "11:00")["available"] is True


def test_approval_rejects_overlapping_approved_request(
    board_user, officer, place, start_at, end_at
):
    first = make_request(board_user, place, start_at, end_at, title="First")
    second = make_request(
        board_user,
        place,
        start_at + timedelta(minutes=30),
        end_at + timedelta(minutes=30),
        title="Second",
    )

    approve_session_request(request_id=first.pk, reviewer=officer)

    with pytest.raises(ValidationError):
        approve_session_request(request_id=second.pk, reviewer=officer)

    second.refresh_from_db()
    assert second.status == SessionRequest.Status.PENDING


def test_rejection_requires_reason_and_is_terminal(
    board_user, officer, place, start_at, end_at
):
    request = make_request(board_user, place, start_at, end_at)

    with pytest.raises(ValidationError):
        reject_session_request(request_id=request.pk, reviewer=officer, reason="  ")

    reject_session_request(request_id=request.pk, reviewer=officer, reason="Not suitable")
    request.refresh_from_db()
    assert request.status == SessionRequest.Status.REJECTED

    with pytest.raises(ValidationError):
        approve_session_request(request_id=request.pk, reviewer=officer)


def test_approved_cancellation_requires_reason_and_releases_slot(
    board_user, officer, place, tomorrow, start_at, end_at
):
    request = make_request(board_user, place, start_at, end_at)
    approve_session_request(request_id=request.pk, reviewer=officer)

    with pytest.raises(ValidationError):
        cancel_session_request(request_id=request.pk, actor=board_user)

    cancel_session_request(
        request_id=request.pk,
        actor=board_user,
        reason="Board schedule changed",
    )
    request.refresh_from_db()
    assert request.status == SessionRequest.Status.CANCELLED
    assert slot_at(get_available_slots(place, tomorrow), "10:00")["available"] is True


def test_board_account_cannot_approve(board_user, place, start_at, end_at):
    request = make_request(board_user, place, start_at, end_at)

    with pytest.raises(PermissionDenied):
        approve_session_request(request_id=request.pk, reviewer=board_user)


def test_request_start_must_align_to_a_slot(board_user, place, start_at, end_at):
    with pytest.raises(ValidationError):
        make_request(
            board_user,
            place,
            start_at + timedelta(minutes=15),
            end_at + timedelta(minutes=15),
        )


def test_request_outside_opening_hours_is_rejected(
    board_user, place, start_at, end_at
):
    with pytest.raises(ValidationError):
        make_request(
            board_user,
            place,
            start_at - timedelta(hours=2),
            end_at - timedelta(hours=2),
        )


def test_request_in_the_past_is_rejected(board_user, place, start_at, end_at):
    with pytest.raises(ValidationError):
        make_request(
            board_user,
            place,
            start_at - timedelta(days=2),
            end_at - timedelta(days=2),
        )


def test_duration_limits_are_enforced(board_user, place, start_at, end_at):
    with pytest.raises(ValidationError):
        make_request(board_user, place, start_at, start_at + timedelta(minutes=15))

    with pytest.raises(ValidationError):
        make_request(board_user, place, start_at, start_at + timedelta(hours=5))


def test_cross_entity_conflict_is_blocked(
    board_user, other_board_user, officer, place, start_at, end_at
):
    first = make_request(board_user, place, start_at, end_at, title="First board")
    second = make_request(
        other_board_user, place, start_at, end_at, title="Second board"
    )

    approve_session_request(request_id=first.pk, reviewer=officer)

    with pytest.raises(ValidationError):
        approve_session_request(request_id=second.pk, reviewer=officer)


def test_officer_can_approve_another_entity_and_own_request(
    officer, other_board_user, place, start_at, end_at
):
    other_request = make_request(other_board_user, place, start_at, end_at)
    own_request = make_request(
        officer,
        place,
        start_at + timedelta(hours=1),
        end_at + timedelta(hours=1),
        title="Officer session",
    )

    approve_session_request(request_id=other_request.pk, reviewer=officer)
    approve_session_request(request_id=own_request.pk, reviewer=officer)

    other_request.refresh_from_db()
    own_request.refresh_from_db()
    assert other_request.status == SessionRequest.Status.APPROVED
    assert own_request.status == SessionRequest.Status.APPROVED


def test_status_changes_are_recorded_in_history(
    board_user, officer, place, start_at, end_at
):
    request = make_request(board_user, place, start_at, end_at)
    approve_session_request(request_id=request.pk, reviewer=officer)

    history = list(RequestStatusHistory.objects.filter(request=request).order_by("created_at"))

    assert [entry.to_status for entry in history] == [
        SessionRequest.Status.PENDING,
        SessionRequest.Status.APPROVED,
    ]
    assert history[-1].changed_by == officer


def test_admin_sees_all_requests(
    admin_user, board_user, other_board_user, place, start_at, end_at
):
    make_request(board_user, place, start_at, end_at, title="Board one")
    make_request(
        other_board_user,
        place,
        start_at + timedelta(hours=1),
        end_at + timedelta(hours=1),
        title="Board two",
    )

    assert visible_requests_for(admin_user).count() == 2


def test_place_closure_removes_slots(place, tomorrow):
    PlaceClosure.objects.create(
        place=place,
        start_date=tomorrow,
        end_date=tomorrow,
        reason="Holiday",
    )

    assert get_available_slots(place, tomorrow) == []


def test_board_account_must_belong_to_a_board(cs_cis):
    user = User(
        email="invalid-board@example.com",
        full_name="Invalid Board Account",
        unit=cs_cis,
        account_type=User.AccountType.BOARD_ACCOUNT,
    )

    with pytest.raises(ValidationError):
        user.full_clean()


def test_admin_can_create_for_a_board(admin_user, board, cs_cis, place, start_at, end_at):
    request = create_session_request(
        user=admin_user,
        requesting_unit=board,
        place=place,
        start_at=start_at,
        end_at=end_at,
        title="Admin-created session",
        purpose="Created on behalf of the board.",
    )

    assert request.requesting_unit == board
    assert request.reviewer_unit == cs_cis
