from django.core.exceptions import ValidationError
from django.db import models


class Place(models.Model):
    name = models.CharField(max_length=120, unique=True)
    building = models.CharField(max_length=120, blank=True)
    room = models.CharField(max_length=80, blank=True)
    opening_time = models.TimeField()
    closing_time = models.TimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.opening_time and self.closing_time and self.opening_time >= self.closing_time:
            raise ValidationError(
                {"closing_time": "Closing time must be later than opening time."}
            )


class PlaceClosure(models.Model):
    place = models.ForeignKey(Place, on_delete=models.CASCADE, related_name="closures")
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_date", "place__name"]

    def __str__(self):
        return f"{self.place} closed {self.start_date}–{self.end_date}"

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError({"end_date": "End date must be on or after start date."})
