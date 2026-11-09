import json

import pytest
from django.urls import reverse
from pywebpush import WebPushException

from bookings.services import create_session_request
from notifications.models import Notification, PushSubscription
from notifications.push import send_push_for_notification, send_push_to_user
from notifications.services import notify_user

pytestmark = pytest.mark.django_db


def subscription_payload(endpoint="https://push.example.com/subscription"):
    return {
        "endpoint": endpoint,
        "keys": {
            "p256dh": "p256dh-value",
            "auth": "auth-value",
        },
    }


def make_subscription(user, endpoint="https://push.example.com/subscription"):
    return PushSubscription.objects.create(
        user=user,
        endpoint=endpoint,
        p256dh="p256dh-value",
        auth="auth-value",
    )


def test_service_worker_is_served(client):
    response = client.get(reverse("service_worker"))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/javascript")
    assert response["Service-Worker-Allowed"] == "/"
    assert b"addEventListener" in response.content


def test_subscribe_requires_login(client):
    response = client.post(
        reverse("push_subscribe"),
        data=json.dumps(subscription_payload()),
        content_type="application/json",
    )

    assert response.status_code == 302
    assert reverse("login") in response.url


def test_subscribe_creates_subscription(client, board_user):
    client.force_login(board_user)

    response = client.post(
        reverse("push_subscribe"),
        data=json.dumps(subscription_payload()),
        content_type="application/json",
    )

    assert response.status_code == 200
    subscription = PushSubscription.objects.get(
        endpoint="https://push.example.com/subscription"
    )
    assert subscription.user == board_user
    assert subscription.p256dh == "p256dh-value"
    assert subscription.auth == "auth-value"


def test_subscribe_reassigns_an_existing_endpoint(
    client, board_user, other_board_user
):
    make_subscription(other_board_user)
    client.force_login(board_user)

    response = client.post(
        reverse("push_subscribe"),
        data=json.dumps(subscription_payload()),
        content_type="application/json",
    )

    assert response.status_code == 200
    subscription = PushSubscription.objects.get(
        endpoint="https://push.example.com/subscription"
    )
    assert subscription.user == board_user


def test_subscribe_rejects_invalid_json(client, board_user):
    client.force_login(board_user)

    response = client.post(
        reverse("push_subscribe"),
        data="{not-json",
        content_type="application/json",
    )

    assert response.status_code == 400


def test_subscribe_rejects_an_insecure_endpoint(client, board_user):
    client.force_login(board_user)

    response = client.post(
        reverse("push_subscribe"),
        data=json.dumps(subscription_payload("http://example.com/push")),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert not PushSubscription.objects.exists()


def test_subscribe_limits_subscriptions_per_user(client, board_user):
    for index in range(10):
        make_subscription(
            board_user,
            endpoint=f"https://push.example.com/{index}",
        )
    client.force_login(board_user)

    response = client.post(
        reverse("push_subscribe"),
        data=json.dumps(subscription_payload("https://push.example.com/extra")),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert board_user.push_subscriptions.count() == 10


def test_unsubscribe_removes_only_the_current_users_subscription(
    client, board_user, other_board_user
):
    own = make_subscription(board_user, "https://push.example.com/own")
    other = make_subscription(other_board_user, "https://push.example.com/other")
    client.force_login(board_user)

    response = client.post(
        reverse("push_unsubscribe"),
        data=json.dumps({"endpoint": own.endpoint}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert not PushSubscription.objects.filter(pk=own.pk).exists()
    assert PushSubscription.objects.filter(pk=other.pk).exists()


def test_push_service_is_a_noop_without_vapid_keys(
    settings, monkeypatch, board_user
):
    settings.VAPID_PUBLIC_KEY = ""
    settings.VAPID_PRIVATE_KEY = ""
    make_subscription(board_user)
    calls = []
    monkeypatch.setattr(
        "notifications.push.webpush",
        lambda **kwargs: calls.append(kwargs),
    )

    sent = send_push_to_user(board_user, title="Title", body="Body")

    assert sent == 0
    assert calls == []


def test_push_service_sends_the_expected_payload(
    settings, monkeypatch, board_user
):
    settings.VAPID_PUBLIC_KEY = "public-key"
    settings.VAPID_PRIVATE_KEY = "private-key"
    settings.VAPID_SUBJECT = "mailto:test@example.com"
    subscription = make_subscription(board_user)
    calls = []
    monkeypatch.setattr(
        "notifications.push.webpush",
        lambda **kwargs: calls.append(kwargs),
    )

    sent = send_push_to_user(
        board_user,
        title="Session approved",
        body="Your request was approved.",
        url="/requests/123/",
        tag="notification-1",
    )

    assert sent == 1
    assert len(calls) == 1
    assert calls[0]["subscription_info"]["endpoint"] == subscription.endpoint
    assert calls[0]["subscription_info"]["keys"]["p256dh"] == "p256dh-value"
    assert calls[0]["vapid_private_key"] == "private-key"
    assert calls[0]["vapid_claims"] == {"sub": "mailto:test@example.com"}
    payload = json.loads(calls[0]["data"])
    assert payload["title"] == "Session approved"
    assert payload["body"] == "Your request was approved."
    assert payload["url"] == "/requests/123/"
    assert payload["tag"] == "notification-1"
    subscription.refresh_from_db()
    assert subscription.last_used_at is not None


def test_push_service_deletes_a_gone_subscription(
    settings, monkeypatch, board_user
):
    settings.VAPID_PUBLIC_KEY = "public-key"
    settings.VAPID_PRIVATE_KEY = "private-key"
    subscription = make_subscription(board_user)

    class GoneResponse:
        status_code = 410
        text = "gone"

    def raise_gone(**kwargs):
        raise WebPushException("gone", response=GoneResponse())

    monkeypatch.setattr("notifications.push.webpush", raise_gone)

    sent = send_push_to_user(board_user, title="Title", body="Body")

    assert sent == 0
    assert not PushSubscription.objects.filter(pk=subscription.pk).exists()


def test_notify_user_schedules_a_push_on_commit(
    django_capture_on_commit_callbacks, settings, monkeypatch, board_user
):
    settings.VAPID_PUBLIC_KEY = "public-key"
    settings.VAPID_PRIVATE_KEY = "private-key"
    make_subscription(board_user)
    calls = []
    monkeypatch.setattr(
        "notifications.push.webpush",
        lambda **kwargs: calls.append(kwargs),
    )

    with django_capture_on_commit_callbacks(execute=True):
        notify_user(
            user=board_user,
            kind=Notification.Kind.NEW_REQUEST,
            title="A title",
            body="A body",
        )

    assert len(calls) == 1
    payload = json.loads(calls[0]["data"])
    assert payload["title"] == "A title"
    assert payload["url"] == reverse("notification_list")


def test_push_for_notification_links_to_the_request(
    settings, monkeypatch, board_user, place, start_at, end_at
):
    settings.VAPID_PUBLIC_KEY = "public-key"
    settings.VAPID_PRIVATE_KEY = "private-key"
    make_subscription(board_user)
    session_request = create_session_request(
        user=board_user,
        place=place,
        start_at=start_at,
        end_at=end_at,
        title="Push link",
        purpose="Check the deep link.",
    )
    calls = []
    monkeypatch.setattr(
        "notifications.push.webpush",
        lambda **kwargs: calls.append(kwargs),
    )

    notification = notify_user(
        user=board_user,
        kind=Notification.Kind.DECISION,
        title="Approved",
        body="Approved",
        request=session_request,
    )
    calls.clear()

    send_push_for_notification(notification.pk)

    assert len(calls) == 1
    payload = json.loads(calls[0]["data"])
    assert payload["url"] == reverse("request_detail", args=[session_request.pk])
