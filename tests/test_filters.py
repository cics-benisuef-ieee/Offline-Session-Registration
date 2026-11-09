import csv
from datetime import datetime, time, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from bookings.filters import filter_requests
from bookings.models import SessionRequest
from bookings.services import (
    approve_session_request,
    create_session_request,
    reject_session_request,
)

pytestmark = pytest.mark.django_db


def make_request(user, place, start_at, end_at, title, purpose="Filter test session."):
    return create_session_request(
        user=user,
        place=place,
        start_at=start_at,
        end_at=end_at,
        title=title,
        purpose=purpose,
    )


def at(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=ZoneInfo(settings.TIME_ZONE))


@pytest.fixture
def filter_requests_set(board_user, other_board_user, officer, place):
    tomorrow = timezone.localdate() + timedelta(days=1)
    day_after = tomorrow + timedelta(days=1)

    pending = make_request(
        board_user, place, at(tomorrow, 10), at(tomorrow, 11), "Alpha sync"
    )
    approved = make_request(
        other_board_user,
        place,
        at(tomorrow, 12),
        at(tomorrow, 13),
        "Beta meetup",
        purpose="Cross-board planning.",
    )
    rejected = make_request(
        board_user, place, at(day_after, 9), at(day_after, 10), "Gamma review"
    )
    approve_session_request(request_id=approved.pk, reviewer=officer)
    reject_session_request(
        request_id=rejected.pk, reviewer=officer, reason="Duplicate request."
    )
    return {
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "tomorrow": tomorrow,
        "day_after": day_after,
    }


def csv_request_ids(response):
    reader = csv.reader(StringIO(response.content.decode("utf-8-sig")))
    next(reader)
    return {row[0] for row in reader}


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"status": "pending"},
        {"status": "approved"},
        {"status": "rejected"},
        {"status": "not-a-status"},
        {"q": "beta"},
        {"q": "planning"},
        {"date_from": "tomorrow"},
        {"date_to": "tomorrow"},
        {"date_from": "tomorrow", "date_to": "tomorrow"},
        {"status": "pending", "q": "alpha"},
        {"unit": "board"},
        {"place": "place"},
        {"unit": "board", "date_from": "tomorrow", "date_to": "tomorrow"},
    ],
)
def test_filter_parity_across_list_reports_and_csv(
    client, officer, filter_requests_set, place, params
):
    data = dict(params)
    if data.get("date_from") == "tomorrow":
        data["date_from"] = filter_requests_set["tomorrow"].isoformat()
    if data.get("date_to") == "tomorrow":
        data["date_to"] = filter_requests_set["tomorrow"].isoformat()
    if data.get("unit") == "board":
        data["unit"] = str(filter_requests_set["pending"].requesting_unit_id)
    if data.get("place") == "place":
        data["place"] = str(place.pk)

    expected = set(
        filter_requests(
            SessionRequest.objects.all(), data, is_reviewer=True
        ).values_list("pk", flat=True)
    )
    expected_ids = {str(pk) for pk in expected}

    client.force_login(officer)

    list_response = client.get(reverse("request_list"), data)
    list_ids = {
        str(item.pk) for item in list_response.context["page_obj"].object_list
    }
    assert list_ids == expected_ids

    reports_response = client.get(reverse("reports"), data)
    assert reports_response.context["counts"]["total"] == len(expected_ids)

    csv_response = client.get(reverse("export_requests_csv"), data)
    assert csv_request_ids(csv_response) == expected_ids


def test_unit_filter_is_ignored_for_non_reviewers(filter_requests_set):
    data = {"unit": str(filter_requests_set["approved"].requesting_unit_id)}

    filtered = filter_requests(
        SessionRequest.objects.all(), data, is_reviewer=False
    )

    assert filtered.count() == SessionRequest.objects.count()
