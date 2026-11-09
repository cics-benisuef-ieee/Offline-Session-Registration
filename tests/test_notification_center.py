import pytest
from django.urls import reverse

from notifications.models import Notification, NotificationPreference

pytestmark = pytest.mark.django_db


def make_notification(user, *, title="Notification", kind=None, read=False):
    notification = Notification.objects.create(
        user=user,
        kind=kind or Notification.Kind.NEW_REQUEST,
        title=title,
        body="Body text",
    )
    if read:
        notification.mark_read()
    return notification


def test_notification_center_requires_login(client):
    response = client.get(reverse("notification_list"))

    assert response.status_code == 302
    assert reverse("login") in response.url


def test_notification_count_is_per_user(client, board_user, other_board_user):
    make_notification(board_user, title="Own")
    make_notification(other_board_user, title="Other")

    client.force_login(board_user)
    response = client.get(reverse("notification_count"))

    assert response.status_code == 200
    assert response.context["unread_count"] == 1
    assert b"notification-badge" in response.content


def test_notification_list_is_per_user(client, board_user, other_board_user):
    make_notification(board_user, title="Own notification")
    make_notification(other_board_user, title="Other notification")

    client.force_login(board_user)
    response = client.get(reverse("notification_list"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Own notification" in content
    assert "Other notification" not in content
    assert response.context["unread_count"] == 1


def test_mark_notification_read(client, board_user):
    notification = make_notification(board_user)
    client.force_login(board_user)

    response = client.post(reverse("notification_read", args=[notification.pk]))

    assert response.status_code == 302
    notification.refresh_from_db()
    assert notification.read_at is not None
    assert notification.is_read is True


def test_cannot_mark_another_users_notification_read(client, board_user, other_board_user):
    notification = make_notification(other_board_user)
    client.force_login(board_user)

    response = client.post(reverse("notification_read", args=[notification.pk]))

    assert response.status_code == 404
    notification.refresh_from_db()
    assert notification.read_at is None


def test_mark_all_read_only_touches_the_current_user(
    client, board_user, other_board_user
):
    first = make_notification(board_user, title="First")
    second = make_notification(board_user, title="Second")
    other = make_notification(other_board_user, title="Other")
    client.force_login(board_user)

    response = client.post(reverse("notification_read_all"))

    assert response.status_code == 302
    first.refresh_from_db()
    second.refresh_from_db()
    other.refresh_from_db()
    assert first.read_at is not None
    assert second.read_at is not None
    assert other.read_at is None


def test_notification_preferences_get_and_update(client, board_user):
    client.force_login(board_user)

    response = client.get(reverse("notification_preferences"))
    assert response.status_code == 200
    assert response.context["form"].instance.user == board_user

    response = client.post(
        reverse("notification_preferences"),
        {
            "digest_frequency": NotificationPreference.DigestFrequency.WEEKLY,
        },
    )

    assert response.status_code == 302
    preference = NotificationPreference.objects.get(user=board_user)
    assert preference.digest_frequency == NotificationPreference.DigestFrequency.WEEKLY
