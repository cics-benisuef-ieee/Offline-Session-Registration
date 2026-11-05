import uuid

from django.conf import settings
from django.db import models


class SessionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
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
