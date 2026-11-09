from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.utils import timezone

from bookings.models import SessionRequest
from bookings.reports import build_report_context
from bookings.services import (
    approve_session_request,
    cancel_session_request,
    create_session_request,
    reject_session_request,
)

pytestmark = pytest.mark.django_db


def make_request(user, place, start_at, end_at, title):
    return create_session_request(
        user=user,
        place=place,
        start_at=start_at,
        end_at=end_at,
        title=title,
        purpose="Reports test session.",
    )


def at(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=ZoneInfo(settings.TIME_ZONE))


@pytest.fixture
def report_requests(board_user, other_board_user, officer, place):
    tomorrow = timezone.localdate() + timedelta(days=1)
    day_after = tomorrow + timedelta(days=1)

    pending = make_request(
        board_user, place, at(tomorrow, 9), at(tomorrow, 10), "Pending session"
    )
    approved = make_request(
        other_board_user, place, at(tomorrow, 10), at(tomorrow, 11), "Approved session"
    )
    rejected = make_request(
        board_user, place, at(day_after, 9), at(day_after, 10), "Rejected session"
    )
    cancelled = make_request(
        board_user, place, at(day_after, 11), at(day_after, 12), "Cancelled session"
    )
    approve_session_request(request_id=approved.pk, reviewer=officer)
    reject_session_request(
        request_id=rejected.pk, reviewer=officer, reason="Not needed."
    )
    cancel_session_request(
        request_id=cancelled.pk, actor=board_user, reason="Schedule changed."
    )
    return {
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "cancelled": cancelled,
    }


def test_report_context_aggregates_statuses_units_places_and_months(
    report_requests, board_user, other_board_user, place
):
    report = build_report_context(SessionRequest.objects.all())

    assert report["counts"] == {
        "total": 4,
        "pending": 1,
        "approved": 1,
        "rejected": 1,
        "cancelled": 1,
    }
    assert report["approval_rate"] == 50
    assert report["avg_review_hours"] is not None

    unit_rows = {row["requesting_unit__name"]: row for row in report["per_unit"]}
    assert unit_rows[board_user.unit.name]["total"] == 3
    assert unit_rows[board_user.unit.name]["pending"] == 1
    assert unit_rows[board_user.unit.name]["approved"] == 0
    assert unit_rows[other_board_user.unit.name]["approved"] == 1

    place_row = next(
        row for row in report["per_place"] if row["place__name"] == place.name
    )
    assert place_row["total"] == 4
    assert place_row["approved"] == 1
    assert place_row["booked_minutes"] == 60

    assert sum(row["total"] for row in report["monthly"]) == 4
    assert report["charts"]["status"]["data"] == [1, 1, 1, 1]
    assert report["charts"]["units"]["data"]
    assert sum(report["charts"]["monthly"]["total"]) == 4


def test_report_context_is_zero_safe_for_an_empty_queryset():
    report = build_report_context(SessionRequest.objects.none())

    assert report["counts"]["total"] == 0
    assert report["approval_rate"] == 0
    assert report["avg_review_hours"] is None
    assert report["per_unit"] == []
    assert report["per_place"] == []
    assert report["monthly"] == []
    assert report["charts"]["status"]["data"] == [0, 0, 0, 0]
