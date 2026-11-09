import uuid

from django.conf import settings
from django.db import models


class SessionRequestManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class AllSessionRequestManager(models.Manager):
    pass


class BookingSeries(models.Model):
    class Frequency(models.TextChoices):
        WEEKLY = "weekly", "Weekly"
        BIWEEKLY = "biweekly", "Every two weeks"
        MONTHLY = "monthly", "Monthly"

    requesting_unit = models.ForeignKey(
        "units.Unit",
        on_delete=models.PROTECT,
        related_name="booking_series",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="booking_series",
    )
    place = models.ForeignKey(
        "places.Place",
        on_delete=models.PROTECT,
        related_name="booking_series",
    )
    title = models.CharField(max_length=200)
    purpose = models.TextField()
    frequency = models.CharField(max_length=20, choices=Frequency.choices)
    weekday = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="0 = Monday, 6 = Sunday. Used by weekly and biweekly series.",
    )
    start_time = models.TimeField()
    duration_minutes = models.PositiveSmallIntegerField()
    start_date = models.DateField()
    until_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["requesting_unit", "-created_at"],
                name="series_unit_created_idx",
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_frequency_display()})"

    @property
    def weekday_name(self):
        if self.weekday is None:
            return ""
        return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][
            self.weekday
        ]


class SessionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    objects = SessionRequestManager()
    all_objects = AllSessionRequestManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requesting_unit = models.ForeignKey(
        "units.Unit",
        on_delete=models.PROTECT,
        related_name="session_requests",
    )
    reviewer_unit = models.ForeignKey(
        "units.Unit",
        on_delete=models.PROTECT,
        related_name="reviewable_requests",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_session_requests",
    )
    place = models.ForeignKey(
        "places.Place",
        on_delete=models.PROTECT,
        related_name="session_requests",
    )
    series = models.ForeignKey(
        BookingSeries,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="occurrences",
    )
    series_index = models.PositiveIntegerField(null=True, blank=True)
    title = models.CharField(max_length=200)
    purpose = models.TextField()
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_session_requests",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deleted_session_requests",
    )
    deletion_reason = models.TextField(blank=True)
    reminder_24h_sent_at = models.DateTimeField(null=True, blank=True)
    reminder_2h_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        base_manager_name = "all_objects"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_at__gt=models.F("start_at")),
                name="session_request_end_after_start",
            )
        ]
        indexes = [
            models.Index(
                fields=["place", "status", "start_at", "end_at"],
                name="sreq_conflict_idx",
            ),
            models.Index(fields=["-created_at"], name="sreq_created_idx"),
            models.Index(
                fields=["status", "-created_at"],
                name="sreq_status_created_idx",
            ),
            models.Index(
                fields=["requesting_unit", "-created_at"],
                name="sreq_unit_created_idx",
            ),
            models.Index(
                fields=["status", "start_at"],
                name="sreq_status_start_idx",
            ),
            models.Index(fields=["deleted_at"], name="sreq_deleted_idx"),
            models.Index(
                fields=["deleted_at", "status"],
                name="sreq_deleted_status_idx",
            ),
            models.Index(
                fields=["series", "start_at"],
                name="sreq_series_start_idx",
            ),
        ]

    def __str__(self):
        return f"{self.title} — {self.requesting_unit}"

    @property
    def duration_minutes(self):
        return int((self.end_at - self.start_at).total_seconds() // 60)


class RequestStatusHistory(models.Model):
    request = models.ForeignKey(
        SessionRequest,
        on_delete=models.CASCADE,
        related_name="history",
    )
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, choices=SessionRequest.Status.choices)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="request_status_changes",
    )
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.request_id}: {self.from_status or 'new'} → {self.to_status}"


class CalendarFeed(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="calendar_feed",
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_accessed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Calendar feed for {self.user}"
