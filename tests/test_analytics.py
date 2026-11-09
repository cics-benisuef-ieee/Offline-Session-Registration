from datetime import datetime, time, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from bookings.models import SessionRequest
from bookings.reports import build_utilization_heatmap
from bookings.services import approve_session_request, create_session_request
from places.models import PlaceClosure

pytestmark = pytest.mark.django_db


def at(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=ZoneInfo(settings.TIME_ZONE))


def next_monday():
    today = timezone.localdate()
    delta = (0 - today.weekday()) % 7
    return today + timedelta(days=delta or 7)


def make_request(user, place, start_at, end_at, title):
    return create_session_request(
        user=user,
        place=place,
        start_at=start_at,
        end_at=end_at,
        title=title,
        purpose="Analytics test session.",
    )


def make_approved(officer, user, place, start_at, end_at, title):
    session_request = make_request(user, place, start_at, end_at, title)
    approve_session_request(request_id=session_request.pk, reviewer=officer)
    return session_request


def create_approved_directly(user, place, start_at, end_at, title):
    return SessionRequest.objects.create(
        requesting_unit=user.unit,
        reviewer_unit=user.unit.parent,
        submitted_by=user,
        place=place,
        title=title,
        purpose="Direct analytics row.",
        start_at=start_at,
        end_at=end_at,
        status=SessionRequest.Status.APPROVED,
    )


# ---------------------------------------------------------------------------
# Utilization heatmap
# ---------------------------------------------------------------------------


def test_heatmap_denominator_subtracts_closure_days(officer, board_user, place):
    monday = next_monday()
    sunday = monday + timedelta(days=6)
    wednesday = monday + timedelta(days=2)
    make_approved(
        officer,
        board_user,
        place,
        at(monday, 10),
        at(monday, 11),
        "Monday booking",
    )
    PlaceClosure.objects.create(
        place=place,
        start_date=wednesday,
        end_date=wednesday,
        reason="Holiday",
    )

    heatmap = build_utilization_heatmap(
        places=[place],
        date_from=monday,
        date_to=sunday,
    )

    # 9 open hours per day (09:00-18:00), 7 days minus one closure day.
    assert heatmap["available_minutes"] == 6 * 9 * 60
    assert heatmap["booked_minutes"] == 60
    assert heatmap["percent"] == 2

    rows = {row["weekday"]: row for row in heatmap["places"][0]["rows"]}
    monday_cells = {cell["hour"]: cell for cell in rows[0]["cells"]}
    assert monday_cells[10]["booked"] == 60
    assert monday_cells[10]["available"] == 60
    assert monday_cells[10]["percent"] == 100

    wednesday_cells = {cell["hour"]: cell for cell in rows[2]["cells"]}
    assert all(cell["available"] == 0 for cell in wednesday_cells.values())
    assert all(cell["percent"] == 0 for cell in wednesday_cells.values())


def test_heatmap_is_zero_safe_for_empty_and_invalid_ranges():
    empty = build_utilization_heatmap(places=[], date_from=None, date_to=None)
    assert empty["places"] == []
    assert empty["booked_minutes"] == 0
    assert empty["available_minutes"] == 0
    assert empty["percent"] == 0

    monday = next_monday()
    reversed_range = build_utilization_heatmap(
        places=[],
        date_from=monday + timedelta(days=1),
        date_to=monday,
    )
    assert reversed_range["percent"] == 0
    assert reversed_range["places"] == []


def test_heatmap_caps_percentages_at_one_hundred(board_user, place):
    monday = next_monday()
    create_approved_directly(
        board_user,
        place,
        at(monday, 10),
        at(monday, 11),
        "Overlap A",
    )
    create_approved_directly(
        board_user,
        place,
        at(monday, 10),
        at(monday, 11),
        "Overlap B",
    )

    heatmap = build_utilization_heatmap(
        places=[place],
        date_from=monday,
        date_to=monday,
    )

    rows = {row["weekday"]: row for row in heatmap["places"][0]["rows"]}
    monday_cells = {cell["hour"]: cell for cell in rows[0]["cells"]}
    assert monday_cells[10]["percent"] == 100
    assert heatmap["percent"] <= 100
    assert heatmap["booked_minutes"] <= heatmap["available_minutes"]


def test_heatmap_ignores_non_approved_sessions(board_user, place):
    monday = next_monday()
    make_request(board_user, place, at(monday, 10), at(monday, 11), "Pending only")

    heatmap = build_utilization_heatmap(
        places=[place],
        date_from=monday,
        date_to=monday,
    )

    assert heatmap["booked_minutes"] == 0
    assert heatmap["percent"] == 0


# ---------------------------------------------------------------------------
# Audit viewer
# ---------------------------------------------------------------------------


def test_audit_viewer_lists_and_filters_status_history(
    client, officer, board_user, place
):
    session_request = make_approved(
        officer,
        board_user,
        place,
        at(next_monday(), 10),
        at(next_monday(), 11),
        "Audited session",
    )
    client.force_login(officer)

    response = client.get(reverse("reports_audit"))
    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 2
    assert b"Audited session" in response.content

    approved_only = client.get(reverse("reports_audit"), {"status": "approved"})
    assert approved_only.context["page_obj"].paginator.count == 1
    event = approved_only.context["history"][0]
    assert event.to_status == SessionRequest.Status.APPROVED

    by_actor = client.get(reverse("reports_audit"), {"actor": officer.pk})
    assert by_actor.context["page_obj"].paginator.count == 1

    by_title = client.get(reverse("reports_audit"), {"q": "Audited"})
    assert by_title.context["page_obj"].paginator.count == 2

    by_uuid = client.get(reverse("reports_audit"), {"q": str(session_request.pk)})
    assert by_uuid.context["page_obj"].paginator.count == 2


def test_audit_viewer_is_forbidden_for_board_accounts(client, board_user):
    client.force_login(board_user)

    response = client.get(reverse("reports_audit"))

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# XLSX export
# ---------------------------------------------------------------------------


def test_xlsx_export_matches_the_filtered_rows(
    client, officer, board_user, place
):
    monday = next_monday()
    make_request(board_user, place, at(monday, 9), at(monday, 10), "Pending export")
    make_approved(
        officer,
        board_user,
        place,
        at(monday, 10),
        at(monday, 11),
        "Approved export",
    )
    client.force_login(officer)

    response = client.get(reverse("export_requests_xlsx"))
    assert response.status_code == 200
    assert response["Content-Type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    workbook = load_workbook(BytesIO(response.content))
    sheet = workbook.active
    assert sheet.title == "Session requests"
    assert [cell.value for cell in sheet[1]] == [
        "ID",
        "Title",
        "Unit",
        "Place",
        "Start",
        "End",
        "Status",
        "Submitted by",
        "Reviewed by",
        "Reviewed at",
        "Decision reason",
    ]
    assert sheet.max_row == 3

    filtered = client.get(reverse("export_requests_xlsx"), {"status": "approved"})
    filtered_sheet = load_workbook(BytesIO(filtered.content)).active
    assert filtered_sheet.max_row == 2
    assert filtered_sheet.cell(row=2, column=2).value == "Approved export"


def test_xlsx_export_is_forbidden_for_board_accounts(client, board_user):
    client.force_login(board_user)

    response = client.get(reverse("export_requests_xlsx"))

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Utilization view permissions
# ---------------------------------------------------------------------------


def test_utilization_view_renders_for_officers(client, officer):
    client.force_login(officer)

    response = client.get(reverse("reports_utilization"))

    assert response.status_code == 200
    assert b"Utilization" in response.content


def test_utilization_view_is_forbidden_for_board_accounts(client, board_user):
    client.force_login(board_user)

    response = client.get(reverse("reports_utilization"))

    assert response.status_code == 403
