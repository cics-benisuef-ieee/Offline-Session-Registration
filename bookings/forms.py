from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from places.models import Place
from units.models import Unit

from .series import (
    WEEKDAY_CHOICES,
    build_series_occurrences,
)
from .services import (
    MAX_DURATION_MINUTES,
    MIN_DURATION_MINUTES,
    SLOT_MINUTES,
    _validate_request_time,
    create_session_request,
)


def _duration_choices():
    return [
        (minutes, f"{minutes} minutes")
        for minutes in range(MIN_DURATION_MINUTES, MAX_DURATION_MINUTES + 1, SLOT_MINUTES)
    ]


class SessionRequestForm(forms.Form):
    requesting_unit = forms.ModelChoiceField(
        queryset=Unit.objects.none(),
        required=False,
        label="Unit",
    )
    place = forms.ModelChoiceField(
        queryset=Place.objects.filter(is_active=True),
        label="Place",
    )
    title = forms.CharField(max_length=200)
    purpose = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    start_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    duration_minutes = forms.ChoiceField(choices=_duration_choices(), label="Duration")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user and user.is_superuser:
            self.fields["requesting_unit"].queryset = Unit.objects.filter(is_active=True)
            self.fields["requesting_unit"].required = True
        else:
            self.fields.pop("requesting_unit", None)

        self.fields["start_time"].widget.attrs["step"] = 1800
        availability_attrs = {
            "hx-get": reverse("availability"),
            "hx-target": "#availability",
            "hx-trigger": "change",
            "hx-include": "#id_place,#id_date",
            "hx-indicator": "#availability-indicator",
        }
        self.fields["place"].widget.attrs.update(availability_attrs)
        self.fields["date"].widget.attrs.update(availability_attrs)

    def clean(self):
        cleaned = super().clean()
        place = cleaned.get("place")
        on_date = cleaned.get("date")
        start_time = cleaned.get("start_time")
        duration = cleaned.get("duration_minutes")

        if not all([place, on_date, start_time, duration]):
            return cleaned

        tz = ZoneInfo(settings.TIME_ZONE)
        start_at = datetime.combine(on_date, start_time, tzinfo=tz)
        end_at = start_at + timedelta(minutes=int(duration))

        try:
            _validate_request_time(place, start_at, end_at)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc

        cleaned["start_at"] = start_at
        cleaned["end_at"] = end_at
        return cleaned

    def save(self, user):
        return create_session_request(
            user=user,
            requesting_unit=self.cleaned_data.get("requesting_unit"),
            place=self.cleaned_data["place"],
            start_at=self.cleaned_data["start_at"],
            end_at=self.cleaned_data["end_at"],
            title=self.cleaned_data["title"],
            purpose=self.cleaned_data["purpose"],
        )


class BookingSeriesForm(forms.Form):
    requesting_unit = forms.ModelChoiceField(
        queryset=Unit.objects.none(),
        required=False,
        label="Unit",
    )
    place = forms.ModelChoiceField(
        queryset=Place.objects.filter(is_active=True),
        label="Place",
    )
    title = forms.CharField(max_length=200)
    purpose = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    frequency = forms.ChoiceField(
        choices=[
            ("weekly", "Weekly"),
            ("biweekly", "Every two weeks"),
            ("monthly", "Monthly"),
        ]
    )
    weekday = forms.ChoiceField(
        choices=WEEKDAY_CHOICES,
        required=False,
        label="Weekday",
    )
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    start_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    duration_minutes = forms.ChoiceField(choices=_duration_choices(), label="Duration")
    until_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Repeat until",
    )
    occurrence_count = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=52,
        label="Or number of occurrences",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user and user.is_superuser:
            self.fields["requesting_unit"].queryset = Unit.objects.filter(is_active=True)
            self.fields["requesting_unit"].required = True
        else:
            self.fields.pop("requesting_unit", None)

        self.fields["start_time"].widget.attrs["step"] = 1800

    def clean(self):
        cleaned = super().clean()
        place = cleaned.get("place")
        frequency = cleaned.get("frequency")
        weekday = cleaned.get("weekday")
        start_date = cleaned.get("start_date")
        start_time = cleaned.get("start_time")
        duration = cleaned.get("duration_minutes")
        until_date = cleaned.get("until_date")
        occurrence_count = cleaned.get("occurrence_count")

        if frequency in {"weekly", "biweekly"} and weekday in {None, ""}:
            self.add_error("weekday", "Choose a weekday for this repeat frequency.")
        if frequency == "monthly":
            weekday = None

        if start_date and start_date < timezone.localdate():
            self.add_error("start_date", "The series cannot start in the past.")
        if not until_date and not occurrence_count:
            raise forms.ValidationError(
                "Choose an end date or a number of occurrences."
            )
        if until_date and start_date and until_date < start_date:
            self.add_error(
                "until_date",
                "The repeat end date must be on or after the start date.",
            )

        if not all([place, frequency, start_date, start_time, duration]):
            return cleaned
        if self.errors:
            return cleaned

        try:
            occurrences = build_series_occurrences(
                place=place,
                frequency=frequency,
                weekday=int(weekday) if weekday not in {None, ""} else None,
                start_time=start_time,
                duration_minutes=int(duration),
                start_date=start_date,
                until_date=until_date,
                occurrence_count=occurrence_count,
            )
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc

        cleaned["weekday"] = int(weekday) if weekday not in {None, ""} else None
        cleaned["duration_minutes"] = int(duration)
        cleaned["occurrences"] = occurrences
        return cleaned


class RejectForm(forms.Form):
    reason = forms.CharField(
        label="Rejection reason",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class CancelForm(forms.Form):
    reason = forms.CharField(
        label="Cancellation reason",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
