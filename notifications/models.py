from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    class Kind(models.TextChoices):
        NEW_REQUEST = "new_request", "New request"
        DECISION = "decision", "Decision"
        CANCELLATION = "cancellation", "Cancellation"
        DELETION = "deletion", "Deletion"
        REMINDER_24H = "reminder_24h", "24-hour reminder"
        REMINDER_2H = "reminder_2h", "2-hour reminder"
        DIGEST = "digest", "Officer digest"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    request = models.ForeignKey(
        "bookings.SessionRequest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notifications",
    )
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "read_at"], name="notif_user_read_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "request", "kind"],
                condition=models.Q(kind__in=["reminder_24h", "reminder_2h"]),
                name="uniq_reminder_notification",
            ),
        ]

    def __str__(self):
        return f"{self.kind}: {self.title}"

    @property
    def is_read(self):
        return self.read_at is not None

    def mark_read(self):
        if self.read_at is None:
            self.read_at = timezone.now()
            self.save(update_fields=["read_at"])


class NotificationPreference(models.Model):
    class DigestFrequency(models.TextChoices):
        OFF = "off", "Off"
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preference",
    )
    email_new_request = models.BooleanField(default=True)
    email_decision = models.BooleanField(default=True)
    email_cancellation = models.BooleanField(default=True)
    email_reminder = models.BooleanField(default=True)
    digest_frequency = models.CharField(
        max_length=10,
        choices=DigestFrequency.choices,
        default=DigestFrequency.DAILY,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Notification preferences for {self.user}"

    @classmethod
    def for_user(cls, user):
        preference, _ = cls.objects.get_or_create(user=user)
        return preference
