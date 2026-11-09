from django import forms

from .models import NotificationPreference


class NotificationPreferenceForm(forms.ModelForm):
    class Meta:
        model = NotificationPreference
        fields = [
            "email_new_request",
            "email_decision",
            "email_cancellation",
            "email_reminder",
            "digest_frequency",
        ]
        widgets = {
            "digest_frequency": forms.Select(attrs={"class": "form-select"}),
        }
