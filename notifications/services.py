import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Notification
from .push import send_push_for_notification

logger = logging.getLogger(__name__)
User = get_user_model()

REMINDER_KINDS = {
    Notification.Kind.REMINDER_24H,
    Notification.Kind.REMINDER_2H,
}


def _officers():
    return User.objects.filter(is_active=True).filter(
        Q(account_type=User.AccountType.OFFICER) | Q(is_superuser=True)
    )


def _local_window(session_request):
    start = timezone.localtime(session_request.start_at)
    end = timezone.localtime(session_request.end_at)
    return start, end


def _unique_users(users):
    seen = set()
    for user in users:
        if user is not None and user.pk not in seen:
            seen.add(user.pk)
            yield user


def _status_recipients(session_request):
    return list(_unique_users([session_request.submitted_by, *_officers()]))


def notify_user(*, user, kind, title, body, request=None):
    """Write an in-app notification and schedule a browser push.

    Reminder notifications are de-duplicated with ``get_or_create`` so a
    repeated cron run cannot create a second row or push.
    """
    created = True
    if kind in REMINDER_KINDS and request is not None:
        notification, created = Notification.objects.get_or_create(
            user=user,
            request=request,
            kind=kind,
            defaults={"title": title, "body": body},
        )
    else:
        notification = Notification.objects.create(
            user=user,
            kind=kind,
            title=title,
            body=body,
            request=request,
        )

    if created:
        notification_id = notification.pk
        transaction.on_commit(
            lambda: send_push_for_notification(notification_id)
        )
    return notification


def _request_context(session_request):
    start, end = _local_window(session_request)
    return {
        "session_request": session_request,
        "start": start,
        "end": end,
        "request_id": session_request.id,
    }


def notify_new_request(session_request):
    context = _request_context(session_request)
    title = f"New session request: {session_request.title}"
    body = (
        f"{session_request.requesting_unit} requested {session_request.place} "
        f"from {context['start']:%Y-%m-%d %H:%M} to {context['end']:%H:%M}."
    )
    for officer in _officers():
        notify_user(
            user=officer,
            kind=Notification.Kind.NEW_REQUEST,
            title=title,
            body=body,
            request=session_request,
        )


def notify_new_series(series):
    first = series.occurrences.order_by("start_at").first()
    last = series.occurrences.order_by("start_at").last()
    count = series.occurrences.count()
    title = f"New recurring series: {series.title}"
    body = (
        f"{series.requesting_unit} requested {count} "
        f"{series.get_frequency_display().lower()} session(s) at {series.place}."
    )
    if first is not None and last is not None:
        body += (
            f" First: {timezone.localtime(first.start_at):%Y-%m-%d %H:%M}."
            f" Last: {timezone.localtime(last.start_at):%Y-%m-%d %H:%M}."
        )
    for officer in _officers():
        notify_user(
            user=officer,
            kind=Notification.Kind.NEW_REQUEST,
            title=title,
            body=body,
            request=first,
        )


def notify_decision(session_request):
    context = _request_context(session_request)
    title = f"Session request {session_request.status}: {session_request.title}"
    for recipient in _status_recipients(session_request):
        if recipient.pk == session_request.submitted_by_id:
            body = (
                f"Your request for {session_request.place} on "
                f"{context['start']:%Y-%m-%d %H:%M}–{context['end']:%H:%M} "
                f"was {session_request.status}."
            )
        else:
            body = (
                f"{session_request.requesting_unit}'s request for "
                f"{session_request.place} on "
                f"{context['start']:%Y-%m-%d %H:%M}–{context['end']:%H:%M} "
                f"was {session_request.status}."
            )
        if session_request.decision_reason:
            body += f" Reason: {session_request.decision_reason}"
        notify_user(
            user=recipient,
            kind=Notification.Kind.DECISION,
            title=title,
            body=body,
            request=session_request,
        )


def notify_cancellation(session_request, *, reason=""):
    context = _request_context(session_request)
    title = f"Session request cancelled: {session_request.title}"
    for recipient in _status_recipients(session_request):
        if recipient.pk == session_request.submitted_by_id:
            body = (
                f"Your request for {session_request.place} on "
                f"{context['start']:%Y-%m-%d %H:%M}–{context['end']:%H:%M} "
                f"was cancelled."
            )
        else:
            body = (
                f"{session_request.requesting_unit}'s request for "
                f"{session_request.place} on "
                f"{context['start']:%Y-%m-%d %H:%M}–{context['end']:%H:%M} "
                f"was cancelled."
            )
        if reason:
            body += f" Reason: {reason}"
        notify_user(
            user=recipient,
            kind=Notification.Kind.CANCELLATION,
            title=title,
            body=body,
            request=session_request,
        )


def notify_deletion(session_request):
    context = _request_context(session_request)
    title = f"Approved session deleted by admin: {session_request.title}"
    body = (
        f"An admin deleted the approved session for {session_request.place} on "
        f"{context['start']:%Y-%m-%d %H:%M}–{context['end']:%H:%M}. "
        f"Reason: {session_request.deletion_reason}"
    )
    for recipient in _status_recipients(session_request):
        notify_user(
            user=recipient,
            kind=Notification.Kind.DELETION,
            title=title,
            body=body,
            request=session_request,
        )


def _summary_message(session_requests, action, *, for_requester, reason=""):
    first = session_requests[0]
    count = len(session_requests)
    plural = "s" if count != 1 else ""
    place_ids = {item.place_id for item in session_requests}
    place_label = (
        str(first.place) if len(place_ids) == 1 else f"{len(place_ids)} places"
    )
    same_series = (
        first.series_id is not None
        and all(item.series_id == first.series_id for item in session_requests)
    )
    if same_series:
        title = f"Series {action}: {first.series.title}"
        subject = (
            "Your recurring series"
            if for_requester
            else f"{first.requesting_unit}'s recurring series"
        )
        body = (
            f"{subject} at {place_label} was {action} for "
            f"{count} occurrence{plural}."
        )
    else:
        title = f"Requests {action}"
        subject = "Your" if for_requester else f"{first.requesting_unit}'s"
        verb = "were" if count != 1 else "was"
        body = (
            f"{subject} {count} request{plural} at {place_label} "
            f"{verb} {action}."
        )
    if reason:
        body += f" Reason: {reason}"
    return title, body


def notify_decision_summary(session_requests, action):
    """Send one decision summary per requester and one to officers."""
    requests = list(session_requests)
    if not requests:
        return

    by_requester = {}
    for session_request in requests:
        by_requester.setdefault(
            session_request.submitted_by_id, []
        ).append(session_request)

    notified_user_ids = set()
    for requester_id, requester_requests in by_requester.items():
        requester = requester_requests[0].submitted_by
        reason = requester_requests[0].decision_reason
        title, body = _summary_message(
            requester_requests,
            action,
            for_requester=True,
            reason=reason,
        )
        notify_user(
            user=requester,
            kind=Notification.Kind.DECISION,
            title=title,
            body=body,
            request=requester_requests[0],
        )
        notified_user_ids.add(requester_id)

    reason = requests[0].decision_reason
    title, body = _summary_message(
        requests,
        action,
        for_requester=False,
        reason=reason,
    )
    for officer in _officers():
        if officer.pk in notified_user_ids:
            continue
        notify_user(
            user=officer,
            kind=Notification.Kind.DECISION,
            title=title,
            body=body,
            request=requests[0],
        )


def notify_cancellation_summary(session_requests, *, reason=""):
    """Send one cancellation summary per requester and one to officers."""
    requests = list(session_requests)
    if not requests:
        return

    by_requester = {}
    for session_request in requests:
        by_requester.setdefault(
            session_request.submitted_by_id, []
        ).append(session_request)

    notified_user_ids = set()
    for requester_id, requester_requests in by_requester.items():
        requester = requester_requests[0].submitted_by
        title, body = _summary_message(
            requester_requests,
            "cancelled",
            for_requester=True,
            reason=reason,
        )
        notify_user(
            user=requester,
            kind=Notification.Kind.CANCELLATION,
            title=title,
            body=body,
            request=requester_requests[0],
        )
        notified_user_ids.add(requester_id)

    title, body = _summary_message(
        requests,
        "cancelled",
        for_requester=False,
        reason=reason,
    )
    for officer in _officers():
        if officer.pk in notified_user_ids:
            continue
        notify_user(
            user=officer,
            kind=Notification.Kind.CANCELLATION,
            title=title,
            body=body,
            request=requests[0],
        )


def notify_reminder(session_request, *, hours):
    kind = (
        Notification.Kind.REMINDER_24H
        if hours == 24
        else Notification.Kind.REMINDER_2H
    )
    context = _request_context(session_request)
    title = f"Reminder: {session_request.title} starts in {hours} hours"
    body = (
        f"Your session at {session_request.place} starts on "
        f"{context['start']:%Y-%m-%d %H:%M}."
    )
    return notify_user(
        user=session_request.submitted_by,
        kind=kind,
        title=title,
        body=body,
        request=session_request,
    )


def notify_digest(user, *, pending, upcoming, frequency):
    title = f"Officer digest ({frequency})"
    body = (
        f"{len(pending)} pending request(s) and "
        f"{len(upcoming)} upcoming approved session(s)."
    )
    return notify_user(
        user=user,
        kind=Notification.Kind.DIGEST,
        title=title,
        body=body,
    )
