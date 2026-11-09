import json
import logging

from django.conf import settings
from django.urls import reverse
from pywebpush import WebPushException, webpush

from bookings.models import SessionRequest

from .models import Notification

logger = logging.getLogger(__name__)


def push_configured():
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def _notification_url(notification):
    if notification.request_id:
        exists = SessionRequest.all_objects.filter(
            pk=notification.request_id,
            deleted_at__isnull=True,
        ).exists()
        if exists:
            return reverse("request_detail", args=[notification.request_id])
    return reverse("notification_list")


def _status_code(exc):
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    return status


def send_push_to_user(user, *, title, body, url="", tag="", ttl=86400):
    """Send one Web Push message to every browser registered by ``user``."""
    if not push_configured():
        return 0

    payload = json.dumps(
        {
            "title": title,
            "body": body[:500],
            "url": url or reverse("notification_list"),
            "tag": tag or "",
            "icon": "/static/images/logo.png",
        }
    )
    sent = 0
    for subscription in user.push_subscriptions.all():
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {
                        "p256dh": subscription.p256dh,
                        "auth": subscription.auth,
                    },
                },
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_SUBJECT},
                ttl=ttl,
                timeout=5,
            )
        except WebPushException as exc:
            if _status_code(exc) in (404, 410):
                subscription.delete()
            else:
                logger.warning(
                    "Web push failed for subscription %s (user %s): %s",
                    subscription.pk,
                    user.pk,
                    exc,
                )
        except Exception:
            logger.exception(
                "Unexpected web push failure for subscription %s (user %s)",
                subscription.pk,
                user.pk,
            )
        else:
            subscription.mark_used()
            sent += 1
    return sent


def send_push_for_notification(notification_id):
    notification = (
        Notification.objects.select_related("request")
        .filter(pk=notification_id)
        .first()
    )
    if notification is None:
        return 0
    return send_push_to_user(
        notification.user,
        title=notification.title,
        body=notification.body,
        url=_notification_url(notification),
        tag=f"notification-{notification.pk}",
    )
