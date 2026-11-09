from django import forms

from .models import NotificationPreference


class NotificationPreferenceForm(forms.ModelForm):
    class Meta:
        model = NotificationPreference
        fields = [
            "digest_frequency",
        ]
        widgets = {
            "digest_frequency": forms.Select(attrs={"class": "form-select"}),
        }
        labels = {
            "digest_frequency": "Officer digest",
        }
        help_texts = {
            "digest_frequency": (
                "How often to receive the in-app digest of pending requests "
                "and upcoming sessions."
            ),
        }
