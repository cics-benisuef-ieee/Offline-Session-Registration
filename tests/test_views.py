import json

import pytest
from django.urls import reverse

from accounts.models import User
from bookings.models import SessionRequest
from bookings.services import approve_session_request, create_session_request

pytestmark = pytest.mark.django_db


def make_request(user, place, start_at, end_at, title="Board meeting"):
    return create_session_request(
        user=user,
        place=place,
        start_at=start_at,
        end_at=end_at,
        title=title,
        purpose="Weekly board sync.",
    )


def test_login_is_required(client):
    response = client.get(reverse("dashboard"))

    assert response.status_code == 302
    assert reverse("login") in response.url


def test_login_page_renders(client):
    response = client.get(reverse("login"))

    assert response.status_code == 200
    assert b"Sign in" in response.content


def test_user_can_login_with_email(client, board_user):
    response = client.post(
        reverse("login"),
        {"username": board_user.email, "password": "test-password"},
    )

    assert response.status_code == 302
    assert response.url == reverse("dashboard")


def test_password_reset_page_renders(client):
    response = client.get(reverse("password_reset"))

    assert response.status_code == 200
    assert b"Reset your password" in response.content


def test_dashboard_and_request_list_render(client, board_user, place, start_at, end_at):
    make_request(board_user, place, start_at, end_at)
    client.force_login(board_user)

    assert client.get(reverse("dashboard")).status_code == 200
    assert client.get(reverse("request_list")).status_code == 200
    assert client.get(reverse("request_create")).status_code == 200


def test_review_pages_render(client, officer, board_user, place, start_at, end_at):
    session_request = make_request(board_user, place, start_at, end_at)
    client.force_login(officer)

    assert client.get(reverse("review_queue")).status_code == 200
    assert client.get(reverse("request_review", args=[session_request.pk])).status_code == 200
    assert client.get(reverse("calendar")).status_code == 200
    assert client.get(reverse("request_list")).status_code == 200


def test_admin_pages_render(client, admin_user):
    client.force_login(admin_user)

    assert client.get("/admin/").status_code == 200
    assert client.get("/admin/accounts/user/").status_code == 200
    assert client.get("/admin/accounts/user/add/").status_code == 200
    assert client.get("/admin/bookings/sessionrequest/").status_code == 200


def test_admin_request_change_page_renders(
    client, admin_user, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    client.force_login(admin_user)

    response = client.get(
        f"/admin/bookings/sessionrequest/{session_request.pk}/change/"
    )

    assert response.status_code == 200


def test_admin_can_create_user(client, admin_user, board):
    client.force_login(admin_user)

    response = client.post(
        "/admin/accounts/user/add/",
        {
            "email": "created-by-admin@example.com",
            "full_name": "Created by Admin",
            "unit": board.pk,
            "account_type": User.AccountType.BOARD_ACCOUNT,
            "password1": "strong-test-password",
            "password2": "strong-test-password",
        },
    )

    assert response.status_code == 302
    user = User.objects.get(email="created-by-admin@example.com")
    assert user.unit == board
    assert user.account_type == User.AccountType.BOARD_ACCOUNT


def test_board_user_can_submit_request(
    client, board_user, place, tomorrow
):
    client.force_login(board_user)

    response = client.post(
        reverse("request_create"),
        {
            "place": place.pk,
            "title": "Board meeting",
            "purpose": "Weekly board sync.",
            "date": tomorrow.isoformat(),
            "start_time": "10:00",
            "duration_minutes": "60",
        },
    )

    assert response.status_code == 302
    session_request = SessionRequest.objects.get()
    assert session_request.requesting_unit == board_user.unit
    assert session_request.status == SessionRequest.Status.PENDING

    detail = client.get(reverse("request_detail", args=[session_request.pk]))
    assert detail.status_code == 200
    assert b"Board meeting" in detail.content


def test_board_user_cannot_open_another_boards_request(
    client, board_user, other_board_user, place, start_at, end_at
):
    other_request = make_request(other_board_user, place, start_at, end_at)
    client.force_login(board_user)

    response = client.get(reverse("request_detail", args=[other_request.pk]))

    assert response.status_code == 404


def test_board_user_cannot_access_review_queue(client, board_user):
    client.force_login(board_user)

    response = client.get(reverse("review_queue"))

    assert response.status_code == 403


def test_officer_can_approve_request(
    client, officer, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    client.force_login(officer)

    response = client.post(
        reverse("request_review", args=[session_request.pk]),
        {"action": "approve"},
    )

    assert response.status_code == 302
    session_request.refresh_from_db()
    assert session_request.status == SessionRequest.Status.APPROVED
    assert session_request.reviewer == officer


def test_officer_can_reject_request_with_reason(
    client, officer, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    client.force_login(officer)

    response = client.post(
        reverse("request_review", args=[session_request.pk]),
        {"action": "reject", "reason": "Room unavailable"},
    )

    assert response.status_code == 302
    session_request.refresh_from_db()
    assert session_request.status == SessionRequest.Status.REJECTED
    assert session_request.decision_reason == "Room unavailable"


def test_availability_partial_lists_slots(
    client, board_user, place, tomorrow
):
    client.force_login(board_user)

    response = client.get(
        reverse("availability"),
        {"place": place.pk, "date": tomorrow.isoformat()},
    )

    assert response.status_code == 200
    assert b"10:00" in response.content
    assert b"available" in response.content


def test_availability_json_contract(client, board_user, place, tomorrow):
    client.force_login(board_user)

    response = client.get(
        reverse("availability"),
        {"place": place.pk, "date": tomorrow.isoformat(), "format": "json"},
    )

    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["place_id"] == place.pk
    assert payload["date"] == tomorrow.isoformat()
    assert any(slot["start"][11:16] == "10:00" for slot in payload["slots"])


def test_board_account_sees_busy_slots_without_details(
    client, officer, board_user, other_board_user, place, tomorrow, start_at, end_at
):
    other_request = make_request(
        other_board_user, place, start_at, end_at, title="Other board private session"
    )
    approve_session_request(request_id=other_request.pk, reviewer=officer)
    client.force_login(board_user)

    response = client.get(
        reverse("availability"),
        {"place": place.pk, "date": tomorrow.isoformat()},
    )

    assert response.status_code == 200
    assert b"10:00" in response.content
    assert b"unavailable" in response.content
    assert b"Other board private session" not in response.content


def test_calendar_events_returns_approved_requests(
    client, officer, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    approve_session_request(request_id=session_request.pk, reviewer=officer)
    client.force_login(officer)

    response = client.get(reverse("calendar_events"))

    assert response.status_code == 200
    events = json.loads(response.content)
    assert len(events) == 1
    assert events[0]["id"] == str(session_request.pk)


def test_csv_export_contains_visible_requests(
    client, officer, board_user, place, start_at, end_at
):
    make_request(board_user, place, start_at, end_at, title="Export me")
    client.force_login(officer)

    response = client.get(reverse("export_requests_csv"))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert b"Export me" in response.content


def test_board_account_cannot_export_reports(client, board_user):
    client.force_login(board_user)

    assert client.get(reverse("reports")).status_code == 403
    assert client.get(reverse("export_requests_csv")).status_code == 403


def test_reports_page_renders_for_officer(client, officer):
    client.force_login(officer)

    response = client.get(reverse("reports"))

    assert response.status_code == 200
    assert b"Download CSV" in response.content


def test_admin_can_export_reports(client, admin_user):
    client.force_login(admin_user)

    assert client.get(reverse("reports")).status_code == 200
    assert client.get(reverse("export_requests_csv")).status_code == 200


def test_admin_can_submit_for_a_board(
    client, admin_user, board, place, tomorrow
):
    client.force_login(admin_user)

    response = client.post(
        reverse("request_create"),
        {
            "requesting_unit": board.pk,
            "place": place.pk,
            "title": "Admin-created session",
            "purpose": "Created on behalf of the board.",
            "date": tomorrow.isoformat(),
            "start_time": "10:00",
            "duration_minutes": "60",
        },
    )

    assert response.status_code == 302
    session_request = SessionRequest.objects.get()
    assert session_request.requesting_unit == board


def test_board_user_can_cancel_approved_request(
    client, officer, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    approve_session_request(request_id=session_request.pk, reviewer=officer)
    client.force_login(board_user)

    response = client.post(
        reverse("request_cancel", args=[session_request.pk]),
        {"reason": "Schedule changed"},
    )

    assert response.status_code == 302
    session_request.refresh_from_db()
    assert session_request.status == SessionRequest.Status.CANCELLED
