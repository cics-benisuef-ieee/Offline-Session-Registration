from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from bookings.models import BookingSeries, SessionRequest
from bookings.series import (
    approve_series,
    build_series_occurrences,
    cancel_series,
    create_booking_series,
    reject_series,
    reschedule_series_future,
)
from bookings.services import approve_session_request, create_session_request

pytestmark = pytest.mark.django_db


def approved_request(officer, user, place, start_at, end_at, title="Approved"):
    session_request = create_session_request(
        user=user,
        place=place,
        start_at=start_at,
        end_at=end_at,
        title=title,
        purpose="Exercise recurring series.",
    )
    approve_session_request(request_id=session_request.pk, reviewer=officer)
    return session_request


def tomorrow_date():
    return timezone.localdate() + timedelta(days=1)


def create_series(user, place, *, start_date=None, count=3, title="Weekly sync"):
    start_date = start_date or tomorrow_date()
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


def test_weekly_generation_steps_seven_days(place):
    start_date = tomorrow_date()
    occurrences = build_series_occurrences(
        place=place,
        frequency=BookingSeries.Frequency.WEEKLY,
        weekday=start_date.weekday(),
        start_time=time(10, 0),
        duration_minutes=60,
        start_date=start_date,
        occurrence_count=4,
    )

    assert [item["date"] for item in occurrences] == [
        start_date + timedelta(days=7 * offset) for offset in range(4)
    ]
    assert all(item["is_valid"] and not item["conflict"] for item in occurrences)


def test_biweekly_generation_steps_fourteen_days(place):
    start_date = tomorrow_date()
    occurrences = build_series_occurrences(
        place=place,
        frequency=BookingSeries.Frequency.BIWEEKLY,
        weekday=start_date.weekday(),
        start_time=time(10, 0),
        duration_minutes=60,
        start_date=start_date,
        occurrence_count=3,
    )

    assert [item["date"] for item in occurrences] == [
        start_date + timedelta(days=14 * offset) for offset in range(3)
    ]


def test_monthly_generation_clamps_month_end(place):
    occurrences = build_series_occurrences(
        place=place,
        frequency=BookingSeries.Frequency.MONTHLY,
        weekday=None,
        start_time=time(10, 0),
        duration_minutes=60,
        start_date=date(2030, 1, 31),
        until_date=date(2030, 4, 30),
    )

    assert [item["date"] for item in occurrences] == [
        date(2030, 1, 31),
        date(2030, 2, 28),
        date(2030, 3, 31),
        date(2030, 4, 30),
    ]


def test_generation_marks_approved_conflicts(place, start_at, end_at, officer, board_user):
    approved_request(officer, board_user, place, start_at, end_at)
    on_date = timezone.localtime(start_at).date()
    occurrences = build_series_occurrences(
        place=place,
        frequency=BookingSeries.Frequency.WEEKLY,
        weekday=on_date.weekday(),
        start_time=time(10, 0),
        duration_minutes=60,
        start_date=on_date,
        occurrence_count=1,
    )

    assert len(occurrences) == 1
    assert occurrences[0]["conflict"] is True
    assert "already booked" in occurrences[0]["reason"]


def test_create_series_only_creates_selected_occurrences(
    django_capture_on_commit_callbacks, board_user, place, officer
):
    start_date = tomorrow_date()
    occurrences = build_series_occurrences(
        place=place,
        frequency=BookingSeries.Frequency.WEEKLY,
        weekday=start_date.weekday(),
        start_time=time(10, 0),
        duration_minutes=60,
        start_date=start_date,
        occurrence_count=3,
    )
    selected = [occurrences[0]["date"].isoformat(), occurrences[2]["date"].isoformat()]

    with django_capture_on_commit_callbacks(execute=True):
        series = create_booking_series(
            user=board_user,
            place=place,
            title="Selected weeks",
            purpose="Only two occurrences.",
            frequency=BookingSeries.Frequency.WEEKLY,
            weekday=start_date.weekday(),
            start_time=time(10, 0),
            duration_minutes=60,
            start_date=start_date,
            occurrence_count=3,
            selected_dates=selected,
        )

    created = list(series.occurrences.order_by("start_at"))
    assert len(created) == 2
    assert [item.series_index for item in created] == [0, 1]
    assert all(item.status == SessionRequest.Status.PENDING for item in created)
    assert series.until_date == occurrences[2]["date"]
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [officer.email]


def test_create_series_rejects_a_conflicting_selection(
    place, start_at, end_at, officer, board_user
):
    approved_request(officer, board_user, place, start_at, end_at)
    on_date = timezone.localtime(start_at).date()

    with pytest.raises(ValidationError):
        create_booking_series(
            user=board_user,
            place=place,
            title="Conflicting series",
            purpose="Should not be created.",
            frequency=BookingSeries.Frequency.WEEKLY,
            weekday=on_date.weekday(),
            start_time=time(10, 0),
            duration_minutes=60,
            start_date=on_date,
            occurrence_count=1,
            selected_dates=[on_date.isoformat()],
        )


def test_board_account_cannot_create_a_series_for_another_unit(
    board_user, other_board, place
):
    start_date = tomorrow_date()

    with pytest.raises(PermissionDenied):
        create_booking_series(
            user=board_user,
            requesting_unit=other_board,
            place=place,
            title="Wrong unit",
            purpose="Should not be created.",
            frequency=BookingSeries.Frequency.WEEKLY,
            weekday=start_date.weekday(),
            start_time=time(10, 0),
            duration_minutes=60,
            start_date=start_date,
            occurrence_count=1,
            selected_dates=[start_date.isoformat()],
        )


def test_approve_series_reports_partial_success(
    board_user, other_board_user, officer, place
):
    series = create_series(board_user, place)
    occurrences = list(series.occurrences.order_by("start_at"))
    middle = occurrences[1]
    approved_request(
        officer,
        other_board_user,
        place,
        middle.start_at,
        middle.end_at,
        title="Blocking booking",
    )

    result = approve_series(series_id=series.pk, reviewer=officer)

    assert len(result["succeeded"]) == 2
    assert len(result["failed"]) == 1
    assert result["failed"][0]["id"] == str(middle.pk)
    middle.refresh_from_db()
    assert middle.status == SessionRequest.Status.PENDING


def test_reject_series_rejects_all_pending_occurrences(
    board_user, officer, place
):
    series = create_series(board_user, place, count=2)

    result = reject_series(
        series_id=series.pk,
        reviewer=officer,
        reason="The room is unavailable.",
    )

    assert len(result["succeeded"]) == 2
    assert all(
        occurrence.status == SessionRequest.Status.REJECTED
        for occurrence in series.occurrences.all()
    )


def test_cancel_series_this_only_cancels_the_selected_occurrence(
    board_user, place
):
    series = create_series(board_user, place)
    occurrences = list(series.occurrences.order_by("start_at"))

    result = cancel_series(
        series_id=series.pk,
        actor=board_user,
        scope="this",
        from_occurrence=occurrences[1].pk,
    )

    assert result["succeeded"] == [str(occurrences[1].pk)]
    statuses = {
        item.pk: item.status for item in series.occurrences.order_by("start_at")
    }
    assert statuses[occurrences[0].pk] == SessionRequest.Status.PENDING
    assert statuses[occurrences[1].pk] == SessionRequest.Status.CANCELLED
    assert statuses[occurrences[2].pk] == SessionRequest.Status.PENDING


def test_cancel_series_future_never_touches_past_occurrences(
    board_user, place
):
    series = create_series(board_user, place)
    occurrences = list(series.occurrences.order_by("start_at"))
    past_start = timezone.now() - timedelta(days=2)
    SessionRequest.objects.filter(pk=occurrences[0].pk).update(
        start_at=past_start,
        end_at=past_start + timedelta(minutes=60),
    )

    result = cancel_series(
        series_id=series.pk,
        actor=board_user,
        scope="future",
        from_occurrence=occurrences[0].pk,
    )

    assert set(result["succeeded"]) == {str(occurrences[1].pk), str(occurrences[2].pk)}
    occurrences[0].refresh_from_db()
    assert occurrences[0].status == SessionRequest.Status.PENDING
    occurrences[1].refresh_from_db()
    occurrences[2].refresh_from_db()
    assert occurrences[1].status == SessionRequest.Status.CANCELLED
    assert occurrences[2].status == SessionRequest.Status.CANCELLED


def test_cancel_series_requires_a_reason_for_approved_occurrences(
    board_user, officer, place
):
    series = create_series(board_user, place)
    occurrence = series.occurrences.order_by("start_at").first()
    approve_session_request(request_id=occurrence.pk, reviewer=officer)

    with pytest.raises(ValidationError):
        cancel_series(
            series_id=series.pk,
            actor=board_user,
            scope="this",
            from_occurrence=occurrence.pk,
        )

    result = cancel_series(
        series_id=series.pk,
        actor=board_user,
        reason="Venue unavailable.",
        scope="this",
        from_occurrence=occurrence.pk,
    )
    assert result["succeeded"] == [str(occurrence.pk)]


def test_reschedule_future_leaves_past_occurrences_untouched(
    board_user, place
):
    series = create_series(board_user, place)
    occurrences = list(series.occurrences.order_by("start_at"))
    past_start = timezone.now() - timedelta(days=2)
    SessionRequest.objects.filter(pk=occurrences[0].pk).update(
        start_at=past_start,
        end_at=past_start + timedelta(minutes=60),
    )
    original_past_start = past_start

    result = reschedule_series_future(
        series_id=series.pk,
        actor=board_user,
        from_occurrence=occurrences[0].pk,
        new_start_time=time(14, 0),
    )

    assert set(result["succeeded"]) == {str(occurrences[1].pk), str(occurrences[2].pk)}
    occurrences[0].refresh_from_db()
    assert occurrences[0].start_at == original_past_start
    occurrences[1].refresh_from_db()
    occurrences[2].refresh_from_db()
    assert timezone.localtime(occurrences[1].start_at).time() == time(14, 0)
    assert timezone.localtime(occurrences[2].start_at).time() == time(14, 0)


def test_series_preview_flags_conflicts(
    client, board_user, officer, place, start_at, end_at
):
    approved_request(officer, board_user, place, start_at, end_at)
    on_date = timezone.localtime(start_at).date()
    client.force_login(board_user)

    response = client.post(
        reverse("series_preview"),
        {
            "place": place.pk,
            "title": "Preview",
            "purpose": "Preview conflicts.",
            "frequency": BookingSeries.Frequency.WEEKLY,
            "weekday": on_date.weekday(),
            "start_date": on_date.isoformat(),
            "start_time": "10:00",
            "duration_minutes": "60",
            "occurrence_count": "1",
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Conflict" in content
    assert "already booked" in content


def test_series_create_view_creates_selected_occurrences(
    client, board_user, place
):
    start_date = tomorrow_date()
    occurrences = build_series_occurrences(
        place=place,
        frequency=BookingSeries.Frequency.WEEKLY,
        weekday=start_date.weekday(),
        start_time=time(10, 0),
        duration_minutes=60,
        start_date=start_date,
        occurrence_count=3,
    )
    selected = [occurrences[0]["date"].isoformat(), occurrences[2]["date"].isoformat()]
    client.force_login(board_user)

    response = client.post(
        reverse("series_create"),
        {
            "action": "create",
            "place": place.pk,
            "title": "View series",
            "purpose": "Created through the view.",
            "frequency": BookingSeries.Frequency.WEEKLY,
            "weekday": start_date.weekday(),
            "start_date": start_date.isoformat(),
            "start_time": "10:00",
            "duration_minutes": "60",
            "occurrence_count": "3",
            "selected_dates": selected,
        },
    )

    series = BookingSeries.objects.get(title="View series")
    assert response.status_code == 302
    assert response.url == reverse("series_detail", args=[series.pk])
    assert series.occurrences.count() == 2


def test_series_detail_is_scoped_to_the_unit(client, board_user, other_board_user, place):
    series = create_series(other_board_user, place, title="Other unit series")
    client.force_login(board_user)

    response = client.get(reverse("series_detail", args=[series.pk]))

    assert response.status_code == 404


def test_only_reviewers_can_approve_a_series(client, board_user, place):
    series = create_series(board_user, place)
    client.force_login(board_user)

    response = client.post(reverse("series_approve", args=[series.pk]))

    assert response.status_code == 403


def test_series_cancel_view_uses_the_service(
    client, board_user, place
):
    series = create_series(board_user, place, count=2)
    occurrence = series.occurrences.order_by("start_at").first()
    client.force_login(board_user)

    response = client.post(
        reverse("series_cancel", args=[series.pk]),
        {"scope": "this", "occurrence": occurrence.pk, "reason": ""},
    )

    assert response.status_code == 302
    occurrence.refresh_from_db()
    assert occurrence.status == SessionRequest.Status.CANCELLED


def test_series_reschedule_view_updates_future_pending(
    client, board_user, place
):
    series = create_series(board_user, place, count=2)
    occurrence = series.occurrences.order_by("start_at").first()
    client.force_login(board_user)

    response = client.post(
        reverse("series_reschedule", args=[series.pk]),
        {"occurrence": occurrence.pk, "start_time": "15:00"},
    )

    assert response.status_code == 302
    for item in series.occurrences.order_by("start_at"):
        item.refresh_from_db()
        assert timezone.localtime(item.start_at).time() == time(15, 0)


def test_series_occurrences_appear_on_the_calendar(
    client, board_user, officer, place
):
    series = create_series(board_user, place, count=2)
    approve_series(series_id=series.pk, reviewer=officer)
    client.force_login(officer)

    events = client.get(reverse("calendar_events")).json()

    titles = [event["title"] for event in events]
    assert sum(title.startswith(series.title) for title in titles) == 2


def test_monthly_series_timezone_is_local(place):
    occurrences = build_series_occurrences(
        place=place,
        frequency=BookingSeries.Frequency.MONTHLY,
        weekday=None,
        start_time=time(10, 0),
        duration_minutes=60,
        start_date=date(2030, 1, 31),
        occurrence_count=1,
    )

    assert occurrences[0]["start_at"].tzinfo == ZoneInfo(settings.TIME_ZONE)
