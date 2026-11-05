from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

User = get_user_model()


def _send(subject, message, recipients):
    recipients = sorted({email for email in recipients if email})
    if not recipients:
        return
    transaction.on_commit(
        lambda: send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            recipients,
            fail_silently=True,
        )
    )


def _officer_emails():
    return list(
        User.objects.filter(is_active=True)
        .filter(Q(account_type=User.AccountType.OFFICER) | Q(is_superuser=True))
        .values_list("email", flat=True)
    )


def _local_window(session_request):
    start = timezone.localtime(session_request.start_at)
    end = timezone.localtime(session_request.end_at)
    return start, end


def notify_new_request(session_request):
    start, end = _local_window(session_request)
    subject = f"New session request: {session_request.title}"
    message = (
        f"{session_request.requesting_unit} requested {session_request.place} "
        f"from {start:%Y-%m-%d %H:%M} to {end:%H:%M}.\n\n"
        f"Purpose: {session_request.purpose}\n"
        f"Request ID: {session_request.id}"
    )
    _send(subject, message, _officer_emails())


def notify_decision(session_request):
    start, end = _local_window(session_request)
    subject = f"Session request {session_request.status}: {session_request.title}"
    message = (
        f"Your request for {session_request.place} on "
        f"{start:%Y-%m-%d %H:%M}–{end:%H:%M} was {session_request.status}."
    )
    if session_request.decision_reason:
        message += f"\n\nReason: {session_request.decision_reason}"
    message += f"\n\nRequest ID: {session_request.id}"
    _send(subject, message, [session_request.submitted_by.email])


def notify_cancellation(session_request):
    start, end = _local_window(session_request)
    subject = f"Session request cancelled: {session_request.title}"
    message = (
        f"{session_request.requesting_unit} cancelled the request for "
        f"{session_request.place} on {start:%Y-%m-%d %H:%M}–{end:%H:%M}.\n\n"
        f"Request ID: {session_request.id}"
    )
    _send(subject, message, _officer_emails())
