from datetime import UTC, datetime, time, timedelta
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from icalendar import Calendar

from bookings.models import SessionRequest
from bookings.services import (
    approve_session_request,
    create_session_request,
    get_calendar_feed,
    google_calendar_url,
    reschedule_session_request,
)
from notifications.models import Notification

pytestmark = pytest.mark.django_db


def make_request(user, place, start_at, end_at, title="Calendar session"):
    return create_session_request(
        user=user,
        place=place,
        start_at=start_at,
        end_at=end_at,
        title=title,
        purpose="Calendar integration test.",
    )


def at(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=ZoneInfo(settings.TIME_ZONE))


def test_google_calendar_url_encodes_event_details(board_user, place, start_at, end_at):
    session_request = make_request(board_user, place, start_at, end_at)

    url = google_calendar_url(
        session_request, absolute_url="https://example.com/requests/abc/"
    )
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert parsed.netloc == "calendar.google.com"
    assert params["action"] == ["TEMPLATE"]
    assert params["text"] == [session_request.title]
    assert params["dates"] == [
        f"{start_at.astimezone(UTC):%Y%m%dT%H%M%SZ}/"
        f"{end_at.astimezone(UTC):%Y%m%dT%H%M%SZ}"
    ]
    assert "https://example.com/requests/abc/" in params["details"][0]
    assert "Calendar integration test." in params["details"][0]
    assert place.building in params["location"][0]
    assert place.room in params["location"][0]
    assert params["ctz"] == [settings.TIME_ZONE]


def test_google_calendar_link_only_appears_for_approved_requests(
    client, officer, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    client.force_login(board_user)

    pending = client.get(reverse("request_detail", args=[session_request.pk]))
    assert b"Add to Google Calendar" not in pending.content

    approve_session_request(request_id=session_request.pk, reviewer=officer)
    approved = client.get(reverse("request_detail", args=[session_request.pk]))
    assert b"Add to Google Calendar" in approved.content
    assert b"Download .ics" in approved.content


def test_approval_creates_in_app_notification_without_email(
    django_capture_on_commit_callbacks, board_user, officer, place, start_at, end_at
):
    with django_capture_on_commit_callbacks(execute=True):
        session_request = make_request(board_user, place, start_at, end_at)

    with django_capture_on_commit_callbacks(execute=True):
        approve_session_request(request_id=session_request.pk, reviewer=officer)

    for user in (board_user, officer):
        assert Notification.objects.filter(
            user=user,
            kind=Notification.Kind.DECISION,
            request=session_request,
        ).exists()


def test_calendar_events_pending_overlay_is_officer_only(
    client, officer, board_user, other_board_user, place, start_at, end_at
):
    approved = make_request(board_user, place, start_at, end_at, title="Approved")
    approve_session_request(request_id=approved.pk, reviewer=officer)
    pending = make_request(
        other_board_user,
        place,
        start_at + timedelta(hours=2),
        end_at + timedelta(hours=2),
        title="Pending",
    )

    client.force_login(officer)
    default_events = client.get(reverse("calendar_events")).json()
    assert {event["id"] for event in default_events} == {str(approved.pk)}

    overlay_events = client.get(
        reverse("calendar_events"), {"include_pending": "1"}
    ).json()
    assert {event["id"] for event in overlay_events} == {
        str(approved.pk),
        str(pending.pk),
    }
    statuses = {
        event["id"]: event["extendedProps"]["status"] for event in overlay_events
    }
    assert statuses[str(approved.pk)] == SessionRequest.Status.APPROVED
    assert statuses[str(pending.pk)] == SessionRequest.Status.PENDING

    client.force_login(board_user)
    board_events = client.get(
        reverse("calendar_events"), {"include_pending": "1"}
    ).json()
    assert {event["id"] for event in board_events} == {str(approved.pk)}


def test_board_calendar_does_not_reveal_other_units(
    client, officer, board_user, other_board_user, place, start_at, end_at
):
    own = make_request(board_user, place, start_at, end_at, title="Own")
    other = make_request(
        other_board_user,
        place,
        start_at + timedelta(hours=2),
        end_at + timedelta(hours=2),
        title="Other",
    )
    approve_session_request(request_id=own.pk, reviewer=officer)
    approve_session_request(request_id=other.pk, reviewer=officer)

    client.force_login(board_user)
    events = client.get(reverse("calendar_events")).json()

    assert {event["id"] for event in events} == {str(own.pk)}


def test_owner_can_reschedule_own_pending_request(
    board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    new_start = start_at + timedelta(hours=2)
    new_end = end_at + timedelta(hours=2)

    updated = reschedule_session_request(
        request_id=session_request.pk,
        actor=board_user,
        new_start=new_start,
        new_end=new_end,
    )

    assert updated.start_at == new_start
    assert updated.end_at == new_end
    history = updated.history.last()
    assert history.from_status == SessionRequest.Status.PENDING
    assert history.to_status == SessionRequest.Status.PENDING
    assert "Rescheduled from" in history.reason


def test_owner_cannot_reschedule_own_approved_request(
    officer, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    approve_session_request(request_id=session_request.pk, reviewer=officer)

    with pytest.raises(PermissionDenied):
        reschedule_session_request(
            request_id=session_request.pk,
            actor=board_user,
            new_start=start_at + timedelta(hours=2),
            new_end=end_at + timedelta(hours=2),
        )


def test_officer_can_reschedule_any_request(
    officer, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    approve_session_request(request_id=session_request.pk, reviewer=officer)

    updated = reschedule_session_request(
        request_id=session_request.pk,
        actor=officer,
        new_start=start_at + timedelta(hours=2),
        new_end=end_at + timedelta(hours=2),
    )

    assert updated.status == SessionRequest.Status.APPROVED
    assert updated.start_at == start_at + timedelta(hours=2)


def test_reschedule_conflict_leaves_the_request_unchanged(
    officer, board_user, other_board_user, place, start_at, end_at
):
    first = make_request(board_user, place, start_at, end_at, title="First")
    approve_session_request(request_id=first.pk, reviewer=officer)
    second = make_request(
        other_board_user,
        place,
        start_at + timedelta(hours=2),
        end_at + timedelta(hours=2),
        title="Second",
    )

    with pytest.raises(ValidationError):
        reschedule_session_request(
            request_id=second.pk,
            actor=officer,
            new_start=start_at,
            new_end=end_at,
        )

    second.refresh_from_db()
    assert second.start_at == start_at + timedelta(hours=2)


def test_reschedule_rejects_terminal_requests(
    officer, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    session_request.status = SessionRequest.Status.REJECTED
    session_request.save(update_fields=["status"])

    with pytest.raises(PermissionDenied):
        reschedule_session_request(
            request_id=session_request.pk,
            actor=officer,
            new_start=start_at + timedelta(hours=2),
            new_end=end_at + timedelta(hours=2),
        )


def test_reschedule_view_returns_updated_times(
    client, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    client.force_login(board_user)
    new_start = start_at + timedelta(hours=2)
    new_end = end_at + timedelta(hours=2)

    response = client.post(
        reverse("request_reschedule", args=[session_request.pk]),
        data={
            "start": new_start.isoformat(),
            "end": new_end.isoformat(),
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    session_request.refresh_from_db()
    assert session_request.start_at == new_start
    assert session_request.end_at == new_end


def test_reschedule_view_reports_conflicts(
    client, officer, board_user, other_board_user, place, start_at, end_at
):
    first = make_request(board_user, place, start_at, end_at, title="First")
    approve_session_request(request_id=first.pk, reviewer=officer)
    second = make_request(
        other_board_user,
        place,
        start_at + timedelta(hours=2),
        end_at + timedelta(hours=2),
        title="Second",
    )
    client.force_login(officer)

    response = client.post(
        reverse("request_reschedule", args=[second.pk]),
        data={
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
        },
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "already booked" in response.json()["error"]


def test_board_user_cannot_reschedule_another_units_request(
    client, board_user, other_board_user, place, start_at, end_at
):
    other = make_request(other_board_user, place, start_at, end_at)
    client.force_login(board_user)

    response = client.post(
        reverse("request_reschedule", args=[other.pk]),
        data={
            "start": (start_at + timedelta(hours=2)).isoformat(),
            "end": (end_at + timedelta(hours=2)).isoformat(),
        },
        content_type="application/json",
    )

    assert response.status_code == 404


def test_calendar_feed_is_token_scoped_and_read_only(
    client, officer, board_user, other_board_user, place, start_at, end_at
):
    own = make_request(board_user, place, start_at, end_at, title="Own session")
    other = make_request(
        other_board_user,
        place,
        start_at + timedelta(hours=2),
        end_at + timedelta(hours=2),
        title="Other session",
    )
    pending = make_request(
        board_user,
        place,
        start_at + timedelta(hours=4),
        end_at + timedelta(hours=4),
        title="Pending session",
    )
    approve_session_request(request_id=own.pk, reviewer=officer)
    approve_session_request(request_id=other.pk, reviewer=officer)
    feed = get_calendar_feed(board_user)

    response = client.get(reverse("calendar_feed", args=[feed.token]))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/calendar")
    calendar = Calendar.from_ical(response.content)
    events = [
        component for component in calendar.walk() if component.name == "VEVENT"
    ]
    assert {event["UID"] for event in events} == {str(own.pk)}
    assert str(pending.pk) not in {event["UID"] for event in events}

    event = events[0]
    assert event.decoded("DTSTART") == start_at.astimezone(UTC)
    assert event.decoded("DTEND") == end_at.astimezone(UTC)

    feed.refresh_from_db()
    assert feed.last_accessed_at is not None


def test_calendar_feed_returns_404_for_invalid_or_revoked_tokens(client, board_user):
    feed = get_calendar_feed(board_user)
    old_token = feed.token

    client.force_login(board_user)
    rotate = client.post(reverse("calendar_feed_rotate"))
    assert rotate.status_code == 302

    assert client.get(reverse("calendar_feed", args=[old_token])).status_code == 404
    feed.refresh_from_db()
    assert feed.token != old_token
    assert client.get(reverse("calendar_feed", args=[feed.token])).status_code == 200


def test_request_ics_downloads_only_approved_sessions(
    client, officer, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    client.force_login(board_user)

    pending = client.get(reverse("request_ics", args=[session_request.pk]))
    assert pending.status_code == 404

    approve_session_request(request_id=session_request.pk, reviewer=officer)
    response = client.get(reverse("request_ics", args=[session_request.pk]))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/calendar")
    assert b"BEGIN:VEVENT" in response.content
