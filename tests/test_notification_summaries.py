from datetime import time, timedelta

import pytest
from django.utils import timezone

from bookings.models import BookingSeries, SessionRequest
from bookings.series import (
    approve_series,
    build_series_occurrences,
    cancel_series,
    create_booking_series,
    reject_series,
)
from bookings.services import bulk_decide, create_session_request
from notifications.models import Notification
from places.models import Place

pytestmark = pytest.mark.django_db


def tomorrow_date():
    return timezone.localdate() + timedelta(days=1)


def create_series(user, place, *, count=3, title="Weekly sync"):
    start_date = tomorrow_date()
    weekday = start_date.weekday()
    occurrences = build_series_occurrences(
        place=place,
        frequency=BookingSeries.Frequency.WEEKLY,
        weekday=weekday,
        start_time=time(10, 0),
        duration_minutes=60,
        start_date=start_date,
        occurrence_count=count,
    )
    selected = [occurrence["date"].isoformat() for occurrence in occurrences]
    return create_booking_series(
        user=user,
        place=place,
        title=title,
        purpose="A recurring board meeting.",
        frequency=BookingSeries.Frequency.WEEKLY,
        weekday=weekday,
        start_time=time(10, 0),
        duration_minutes=60,
        start_date=start_date,
        occurrence_count=count,
        selected_dates=selected,
    )


def make_requests(user, place, start_at, end_at, *, count=3):
    requests = []
    for index in range(count):
        offset = timedelta(days=index)
        requests.append(
            create_session_request(
                user=user,
                place=place,
                start_at=start_at + offset,
                end_at=end_at + offset,
                title=f"Bulk request {index}",
                purpose="Exercise bulk notifications.",
            )
        )
    return requests


def test_approve_series_sends_one_summary_per_recipient(
    board_user, officer, place
):
    series = create_series(board_user, place, count=3)

    approve_series(series_id=series.pk, reviewer=officer)

    requester_notifications = Notification.objects.filter(
        user=board_user,
        kind=Notification.Kind.DECISION,
        request__series=series,
    )
    officer_notifications = Notification.objects.filter(
        user=officer,
        kind=Notification.Kind.DECISION,
        request__series=series,
    )
    assert requester_notifications.count() == 1
    assert officer_notifications.count() == 1
    notification = requester_notifications.get()
    assert "Series approved" in notification.title
    assert "3 occurrences" in notification.body


def test_reject_series_sends_one_summary_per_recipient(
    board_user, officer, place
):
    series = create_series(board_user, place, count=3)

    reject_series(
        series_id=series.pk,
        reviewer=officer,
        reason="Not available.",
    )

    for user in (board_user, officer):
        notifications = Notification.objects.filter(
            user=user,
            kind=Notification.Kind.DECISION,
            request__series=series,
        )
        assert notifications.count() == 1
        notification = notifications.get()
        assert "Series rejected" in notification.title
        assert "Not available." in notification.body


def test_cancel_series_sends_one_summary_per_recipient(
    board_user, officer, place
):
    series = create_series(board_user, place, count=3)
    approve_series(series_id=series.pk, reviewer=officer)

    cancel_series(
        series_id=series.pk,
        actor=board_user,
        reason="Schedule changed.",
    )

    for user in (board_user, officer):
        notifications = Notification.objects.filter(
            user=user,
            kind=Notification.Kind.CANCELLATION,
            request__series=series,
        )
        assert notifications.count() == 1
        notification = notifications.get()
        assert "Series cancelled" in notification.title
        assert "3 occurrences" in notification.body
        assert "Schedule changed." in notification.body


def test_bulk_decision_sends_one_summary_per_recipient(
    board_user, officer, place, start_at, end_at
):
    requests = make_requests(board_user, place, start_at, end_at, count=3)

    result = bulk_decide(
        request_ids=[session_request.pk for session_request in requests],
        action="approve",
        reviewer=officer,
    )

    assert len(result["succeeded"]) == 3
    for user in (board_user, officer):
        notifications = Notification.objects.filter(
            user=user,
            kind=Notification.Kind.DECISION,
            request__in=requests,
        )
        assert notifications.count() == 1
        notification = notifications.get()
        assert "Requests approved" in notification.title
        assert "3 requests" in notification.body


def test_bulk_summary_mentions_multiple_places(
    board_user, officer, place, start_at, end_at
):
    other_place = Place.objects.create(
        name="Room B",
        opening_time=time(9, 0),
        closing_time=time(18, 0),
    )
    requests = [
        create_session_request(
            user=board_user,
            place=place,
            start_at=start_at,
            end_at=end_at,
            title="First place",
            purpose="Exercise multi-place summaries.",
        ),
        create_session_request(
            user=board_user,
            place=other_place,
            start_at=start_at,
            end_at=end_at,
            title="Second place",
            purpose="Exercise multi-place summaries.",
        ),
    ]

    bulk_decide(
        request_ids=[session_request.pk for session_request in requests],
        action="approve",
        reviewer=officer,
    )

    notification = Notification.objects.get(
        user=board_user,
        kind=Notification.Kind.DECISION,
        request__in=requests,
    )
    assert "2 requests" in notification.body
    assert "2 places" in notification.body


def test_bulk_rejection_keeps_the_shared_reason(
    board_user, officer, place, start_at, end_at
):
    requests = make_requests(board_user, place, start_at, end_at, count=2)

    bulk_decide(
        request_ids=[session_request.pk for session_request in requests],
        action="reject",
        reviewer=officer,
        reason="Room unavailable.",
    )

    notification = Notification.objects.get(
        user=board_user,
        kind=Notification.Kind.DECISION,
        request__in=requests,
    )
    assert "Requests rejected" in notification.title
    assert "Room unavailable." in notification.body
    assert (
        SessionRequest.objects.filter(
            pk__in=[session_request.pk for session_request in requests],
            status=SessionRequest.Status.REJECTED,
        ).count()
        == 2
    )
