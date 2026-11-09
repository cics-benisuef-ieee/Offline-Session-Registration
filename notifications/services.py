import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone

from .models import Notification, NotificationPreference

logger = logging.getLogger(__name__)
User = get_user_model()

REMINDER_KINDS = {
    Notification.Kind.REMINDER_24H,
    Notification.Kind.REMINDER_2H,
}

EMAIL_PREFERENCE_BY_KIND = {
    Notification.Kind.NEW_REQUEST: "email_new_request",
    Notification.Kind.DECISION: "email_decision",
    Notification.Kind.DELETION: "email_decision",
    Notification.Kind.CANCELLATION: "email_cancellation",
    Notification.Kind.REMINDER_24H: "email_reminder",
    Notification.Kind.REMINDER_2H: "email_reminder",
}


def _officers():
    return User.objects.filter(is_active=True).filter(
        Q(account_type=User.AccountType.OFFICER) | Q(is_superuser=True)
    )


def _local_window(session_request):
    start = timezone.localtime(session_request.start_at)
    end = timezone.localtime(session_request.end_at)
    return start, end


def _email_allowed(user, kind):
    preference = NotificationPreference.for_user(user)
    if kind == Notification.Kind.DIGEST:
        return preference.digest_frequency != NotificationPreference.DigestFrequency.OFF
    field = EMAIL_PREFERENCE_BY_KIND.get(kind)
    if not field:
        return False
    return bool(getattr(preference, field))


def _send_email(*, recipient, subject, text_body, html_body=""):
    if not recipient:
        return

    def send():
        try:
            message = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[recipient],
            )
            if html_body:
                message.attach_alternative(html_body, "text/html")
            message.send(fail_silently=False)
        except Exception:
            logger.exception("Notification email to %s failed.", recipient)

    transaction.on_commit(send)


def render_email(template_name, context):
    base = f"notifications/email/{template_name}"
    text_body = render_to_string(f"{base}.txt", context)
    html_body = render_to_string(f"{base}.html", context)
    return text_body, html_body


def notify_user(
    *,
    user,
    kind,
    title,
    body,
    request=None,
    email_subject="",
    email_text="",
    email_html="",
):
    """Write an in-app notification and optionally schedule its email.

    Reminder notifications are de-duplicated with ``get_or_create`` so a
    repeated cron run cannot create a second row or email.
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

    if (
        created
        and email_subject
        and _email_allowed(user, kind)
    ):
        _send_email(
            recipient=user.email,
            subject=email_subject,
            text_body=email_text,
            html_body=email_html,
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
    text_body, html_body = render_email("new_request", context)
    for officer in _officers():
        notify_user(
            user=officer,
            kind=Notification.Kind.NEW_REQUEST,
            title=title,
            body=body,
            request=session_request,
            email_subject=title,
            email_text=text_body,
            email_html=html_body,
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
    context = {
        "series": series,
        "count": count,
        "first": first,
        "last": last,
    }
    text_body, html_body = render_email("new_series", context)
    for officer in _officers():
        notify_user(
            user=officer,
            kind=Notification.Kind.NEW_REQUEST,
            title=title,
            body=body,
            request=first,
            email_subject=title,
            email_text=text_body,
            email_html=html_body,
        )


def notify_decision(session_request):
    context = _request_context(session_request)
    title = f"Session request {session_request.status}: {session_request.title}"
    body = (
        f"Your request for {session_request.place} on "
        f"{context['start']:%Y-%m-%d %H:%M}–{context['end']:%H:%M} "
        f"was {session_request.status}."
    )
    if session_request.decision_reason:
        body += f" Reason: {session_request.decision_reason}"
    if session_request.status == "approved":
        from bookings.services import google_calendar_url

        context["google_calendar_url"] = google_calendar_url(session_request)

    text_body, html_body = render_email("decision", context)
    notify_user(
        user=session_request.submitted_by,
        kind=Notification.Kind.DECISION,
        title=title,
        body=body,
        request=session_request,
        email_subject=title,
        email_text=text_body,
        email_html=html_body,
    )


def notify_cancellation(session_request):
    context = _request_context(session_request)
    title = f"Session request cancelled: {session_request.title}"
    body = (
        f"{session_request.requesting_unit} cancelled the request for "
        f"{session_request.place} on "
        f"{context['start']:%Y-%m-%d %H:%M}–{context['end']:%H:%M}."
    )
    text_body, html_body = render_email("cancellation", context)
    for officer in _officers():
        notify_user(
            user=officer,
            kind=Notification.Kind.CANCELLATION,
            title=title,
            body=body,
            request=session_request,
            email_subject=title,
            email_text=text_body,
            email_html=html_body,
        )


def notify_deletion(session_request):
    context = _request_context(session_request)
    title = f"Approved session deleted by admin: {session_request.title}"
    body = (
        f"An admin deleted the approved session for {session_request.place} on "
        f"{context['start']:%Y-%m-%d %H:%M}–{context['end']:%H:%M}. "
        f"Reason: {session_request.deletion_reason}"
    )
    context["deletion_reason"] = session_request.deletion_reason
    text_body, html_body = render_email("deletion", context)
    recipients = [session_request.submitted_by, *_officers()]
    seen = set()
    for recipient in recipients:
        if recipient.pk in seen:
            continue
        seen.add(recipient.pk)
        notify_user(
            user=recipient,
            kind=Notification.Kind.DELETION,
            title=title,
            body=body,
            request=session_request,
            email_subject=title,
            email_text=text_body,
            email_html=html_body,
        )


def notify_reminder(session_request, *, hours):
    kind = (
        Notification.Kind.REMINDER_24H
        if hours == 24
        else Notification.Kind.REMINDER_2H
    )
    context = _request_context(session_request)
    context["hours"] = hours
    title = f"Reminder: {session_request.title} starts in {hours} hours"
    body = (
        f"Your session at {session_request.place} starts on "
        f"{context['start']:%Y-%m-%d %H:%M}."
    )
    text_body, html_body = render_email("reminder", context)
    return notify_user(
        user=session_request.submitted_by,
        kind=kind,
        title=title,
        body=body,
        request=session_request,
        email_subject=title,
        email_text=text_body,
        email_html=html_body,
    )


def notify_digest(user, *, pending, upcoming, frequency):
    title = f"Officer digest ({frequency})"
    body = (
        f"{len(pending)} pending request(s) and "
        f"{len(upcoming)} upcoming approved session(s)."
    )
    context = {
        "pending": pending,
        "upcoming": upcoming,
        "frequency": frequency,
    }
    text_body, html_body = render_email("digest", context)
    return notify_user(
        user=user,
        kind=Notification.Kind.DIGEST,
        title=title,
        body=body,
        email_subject=title,
        email_text=text_body,
        email_html=html_body,
    )
