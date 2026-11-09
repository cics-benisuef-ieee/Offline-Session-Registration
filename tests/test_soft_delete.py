from datetime import timedelta
from io import StringIO

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection
from django.urls import reverse
from django.utils import timezone
from icalendar import Calendar

from bookings.models import SessionRequest
from bookings.services import (
    approve_session_request,
    create_session_request,
    delete_session_request,
    get_available_slots,
    get_calendar_feed,
    restore_session_request,
    visible_requests_for,
)

pytestmark = pytest.mark.django_db


def make_request(user, place, start_at, end_at, title="Soft delete session"):
    return create_session_request(
        user=user,
        place=place,
        start_at=start_at,
        end_at=end_at,
        title=title,
        purpose="Exercise the soft-delete workflow.",
    )


def approved_request(officer, user, place, start_at, end_at, title="Approved"):
    session_request = make_request(user, place, start_at, end_at, title=title)
    approve_session_request(request_id=session_request.pk, reviewer=officer)
    return session_request


def slot_at(slots, hhmm):
    return next(slot for slot in slots if slot["start"][11:16] == hhmm)


def test_only_superusers_can_delete_or_restore(
    admin_user, officer, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)

    for actor in (officer, board_user):
        with pytest.raises(PermissionDenied):
            delete_session_request(
                request_id=session_request.pk,
                actor=actor,
                reason="Not allowed.",
            )

    delete_session_request(
        request_id=session_request.pk,
        actor=admin_user,
        reason="Created by mistake.",
    )

    for actor in (officer, board_user):
        with pytest.raises(PermissionDenied):
            restore_session_request(request_id=session_request.pk, actor=actor)


def test_delete_requires_a_reason(admin_user, board_user, place, start_at, end_at):
    session_request = make_request(board_user, place, start_at, end_at)

    with pytest.raises(ValidationError):
        delete_session_request(
            request_id=session_request.pk,
            actor=admin_user,
            reason="   ",
        )

    assert SessionRequest.objects.filter(pk=session_request.pk).exists()


def test_soft_delete_stamps_audit_fields_and_history(
    admin_user, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)

    delete_session_request(
        request_id=session_request.pk,
        actor=admin_user,
        reason="Duplicate record.",
    )

    session_request.refresh_from_db()
    assert session_request.deleted_at is not None
    assert session_request.deleted_by == admin_user
    assert session_request.deletion_reason == "Duplicate record."
    assert session_request.status == SessionRequest.Status.PENDING

    history = session_request.history.last()
    assert history.changed_by == admin_user
    assert history.from_status == SessionRequest.Status.PENDING
    assert history.to_status == SessionRequest.Status.PENDING
    assert history.reason == "Deleted by admin: Duplicate record."


def test_deleted_rows_are_hidden_from_the_default_manager(
    admin_user, officer, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)

    delete_session_request(
        request_id=session_request.pk,
        actor=admin_user,
        reason="Test row.",
    )

    assert not SessionRequest.objects.filter(pk=session_request.pk).exists()
    assert SessionRequest.all_objects.filter(pk=session_request.pk).exists()
    assert not visible_requests_for(officer).filter(pk=session_request.pk).exists()
    assert not visible_requests_for(board_user).filter(pk=session_request.pk).exists()


def test_delete_twice_is_rejected(admin_user, board_user, place, start_at, end_at):
    session_request = make_request(board_user, place, start_at, end_at)
    delete_session_request(
        request_id=session_request.pk,
        actor=admin_user,
        reason="First delete.",
    )

    with pytest.raises(ValidationError):
        delete_session_request(
            request_id=session_request.pk,
            actor=admin_user,
            reason="Second delete.",
        )


def test_deleted_requests_disappear_from_officer_surfaces(
    client, admin_user, officer, board_user, place, start_at, end_at
):
    session_request = approved_request(
        officer, board_user, place, start_at, end_at, title="Hidden approved"
    )
    delete_session_request(
        request_id=session_request.pk,
        actor=admin_user,
        reason="Incorrect booking.",
    )
    client.force_login(officer)

    request_list = client.get(reverse("request_list"))
    assert session_request.title not in request_list.content.decode()

    dashboard = client.get(reverse("dashboard"))
    assert dashboard.context["counts"]["total"] == 0

    reports = client.get(reverse("reports"))
    assert reports.context["counts"]["total"] == 0

    csv_response = client.get(reverse("export_requests_csv"))
    assert str(session_request.pk) not in csv_response.content.decode("utf-8-sig")

    detail = client.get(reverse("request_detail", args=[session_request.pk]))
    assert detail.status_code == 404


def test_deleted_pending_request_leaves_the_review_queue(
    client, admin_user, officer, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    delete_session_request(
        request_id=session_request.pk,
        actor=admin_user,
        reason="Submitted twice.",
    )
    client.force_login(officer)

    response = client.get(reverse("review_queue"))

    assert list(response.context["pending"]) == []


def test_deleted_approved_session_disappears_from_calendar_and_feed(
    client, admin_user, officer, board_user, place, start_at, end_at
):
    session_request = approved_request(officer, board_user, place, start_at, end_at)
    delete_session_request(
        request_id=session_request.pk,
        actor=admin_user,
        reason="No longer happening.",
    )
    client.force_login(officer)

    events = client.get(reverse("calendar_events")).json()
    assert events == []

    feed_response = client.get(
        reverse("calendar_feed", args=[get_calendar_feed(board_user).token])
    )
    calendar = Calendar.from_ical(feed_response.content)
    assert [
        component for component in calendar.walk() if component.name == "VEVENT"
    ] == []


def test_deleting_an_approved_session_frees_the_slot(
    admin_user, officer, board_user, other_board_user, place, tomorrow, start_at, end_at
):
    first = approved_request(officer, board_user, place, start_at, end_at, title="First")
    second = make_request(
        other_board_user, place, start_at, end_at, title="Replacement"
    )

    with pytest.raises(ValidationError):
        approve_session_request(request_id=second.pk, reviewer=officer)

    delete_session_request(
        request_id=first.pk,
        actor=admin_user,
        reason="Cancelled externally.",
    )

    assert slot_at(get_available_slots(place, tomorrow), "10:00")["available"] is True
    approve_session_request(request_id=second.pk, reviewer=officer)
    second.refresh_from_db()
    assert second.status == SessionRequest.Status.APPROVED


def test_delete_notifies_requester_and_officers_for_approved_sessions(
    django_capture_on_commit_callbacks,
    admin_user,
    officer,
    board_user,
    place,
    start_at,
    end_at,
):
    with django_capture_on_commit_callbacks(execute=True):
        session_request = approved_request(
            officer, board_user, place, start_at, end_at
        )
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        delete_session_request(
            request_id=session_request.pk,
            actor=admin_user,
            reason="Venue unavailable.",
        )

    recipients = {email for message in mail.outbox for email in message.to}
    assert board_user.email in recipients
    assert officer.email in recipients
    assert all("Venue unavailable." in message.body for message in mail.outbox)


def test_delete_does_not_notify_for_pending_sessions(
    django_capture_on_commit_callbacks,
    admin_user,
    officer,
    board_user,
    place,
    start_at,
    end_at,
):
    with django_capture_on_commit_callbacks(execute=True):
        session_request = make_request(board_user, place, start_at, end_at)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        delete_session_request(
            request_id=session_request.pk,
            actor=admin_user,
            reason="Test row.",
        )

    assert mail.outbox == []


def test_restore_clears_the_deletion_fields(
    admin_user, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    delete_session_request(
        request_id=session_request.pk,
        actor=admin_user,
        reason="Accidental delete.",
    )

    restored = restore_session_request(
        request_id=session_request.pk,
        actor=admin_user,
    )

    assert restored.deleted_at is None
    assert restored.deleted_by is None
    assert restored.deletion_reason == ""
    assert SessionRequest.objects.filter(pk=session_request.pk).exists()
    assert restored.history.last().reason == "Restored by admin"


def test_restore_refuses_a_now_conflicting_approved_slot(
    admin_user, officer, board_user, other_board_user, place, start_at, end_at
):
    first = approved_request(officer, board_user, place, start_at, end_at, title="First")
    delete_session_request(
        request_id=first.pk,
        actor=admin_user,
        reason="Accidental delete.",
    )
    replacement = make_request(
        other_board_user, place, start_at, end_at, title="Replacement"
    )
    approve_session_request(request_id=replacement.pk, reviewer=officer)

    with pytest.raises(ValidationError):
        restore_session_request(request_id=first.pk, actor=admin_user)

    first.refresh_from_db()
    assert first.deleted_at is not None


def test_admin_soft_delete_and_restore_actions(
    client, admin_user, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    client.force_login(admin_user)
    changelist = reverse("admin:bookings_sessionrequest_changelist")

    confirmation = client.post(
        changelist,
        {
            "action": "soft_delete_selected",
            "_selected_action": [str(session_request.pk)],
        },
    )
    assert confirmation.status_code == 200
    assert b"Deletion reason" in confirmation.content

    response = client.post(
        changelist,
        {
            "action": "soft_delete_selected",
            "apply": "1",
            "_selected_action": [str(session_request.pk)],
            "reason": "Created during testing.",
        },
        follow=True,
    )
    assert response.status_code == 200
    assert not SessionRequest.objects.filter(pk=session_request.pk).exists()
    assert SessionRequest.all_objects.get(pk=session_request.pk).deleted_at is not None

    deleted_filter = client.get(changelist, {"deleted": "deleted"})
    assert session_request.title in deleted_filter.content.decode()

    response = client.post(
        changelist,
        {
            "action": "restore_selected",
            "_selected_action": [str(session_request.pk)],
        },
        follow=True,
    )
    assert response.status_code == 200
    restored = SessionRequest.all_objects.get(pk=session_request.pk)
    assert restored.deleted_at is None
    assert SessionRequest.objects.filter(pk=session_request.pk).exists()


def test_admin_soft_delete_rejects_a_blank_reason(
    client, admin_user, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    client.force_login(admin_user)

    response = client.post(
        reverse("admin:bookings_sessionrequest_changelist"),
        {
            "action": "soft_delete_selected",
            "apply": "1",
            "_selected_action": [str(session_request.pk)],
            "reason": "   ",
        },
    )

    assert response.status_code == 200
    assert SessionRequest.objects.filter(pk=session_request.pk).exists()


def test_purge_command_is_dry_run_by_default_and_commits(
    admin_user, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    delete_session_request(
        request_id=session_request.pk,
        actor=admin_user,
        reason="Old test row.",
    )
    SessionRequest.all_objects.filter(pk=session_request.pk).update(
        deleted_at=timezone.now() - timedelta(days=60)
    )

    stdout = StringIO()
    call_command("purge_deleted_sessions", older_than=30, stdout=stdout)

    assert "would be purged" in stdout.getvalue()
    assert SessionRequest.all_objects.filter(pk=session_request.pk).exists()

    stdout = StringIO()
    call_command(
        "purge_deleted_sessions",
        older_than=30,
        commit=True,
        stdout=stdout,
    )

    assert "Permanently purged" in stdout.getvalue()
    assert not SessionRequest.all_objects.filter(pk=session_request.pk).exists()
    assert not session_request.history.exists()


def test_purge_command_keeps_recent_deletions(
    admin_user, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    delete_session_request(
        request_id=session_request.pk,
        actor=admin_user,
        reason="Recent test row.",
    )

    call_command("purge_deleted_sessions", older_than=30, commit=True, stdout=StringIO())

    assert SessionRequest.all_objects.filter(pk=session_request.pk).exists()


def test_request_delete_view_is_superuser_only(
    client, officer, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)

    for actor in (officer, board_user):
        client.force_login(actor)
        response = client.post(
            reverse("request_delete", args=[session_request.pk]),
            {"reason": "Not allowed."},
        )
        assert response.status_code == 403

    assert SessionRequest.objects.filter(pk=session_request.pk).exists()


def test_request_delete_view_requires_a_reason(
    client, admin_user, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    client.force_login(admin_user)

    response = client.post(
        reverse("request_delete", args=[session_request.pk]),
        {"reason": "  "},
        follow=True,
    )

    assert response.status_code == 200
    assert SessionRequest.objects.filter(pk=session_request.pk).exists()


def test_request_delete_view_soft_deletes_and_redirects(
    client, admin_user, board_user, place, start_at, end_at
):
    session_request = make_request(board_user, place, start_at, end_at)
    client.force_login(admin_user)

    response = client.post(
        reverse("request_delete", args=[session_request.pk]),
        {"reason": "Wrong date."},
    )

    assert response.status_code == 302
    assert response.url == reverse("request_list")
    assert not SessionRequest.objects.filter(pk=session_request.pk).exists()
    assert SessionRequest.all_objects.get(pk=session_request.pk).deletion_reason == (
        "Wrong date."
    )


@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="The partial exclusion constraint only exists on PostgreSQL.",
)
def test_partial_exclusion_constraint_allows_deleted_approved_overlap(
    admin_user, officer, board_user, other_board_user, place, start_at, end_at
):
    first = approved_request(officer, board_user, place, start_at, end_at, title="First")
    delete_session_request(
        request_id=first.pk,
        actor=admin_user,
        reason="Replaced by another request.",
    )
    replacement = make_request(
        other_board_user, place, start_at, end_at, title="Replacement"
    )

    approve_session_request(request_id=replacement.pk, reviewer=officer)

    replacement.refresh_from_db()
    assert replacement.status == SessionRequest.Status.APPROVED
