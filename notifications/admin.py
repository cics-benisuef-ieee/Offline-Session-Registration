from django.contrib import admin

from .models import Notification, NotificationPreference


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["title", "user", "kind", "read_at", "created_at"]
    list_filter = ["kind", "read_at"]
    search_fields = ["title", "body", "user__email"]
    autocomplete_fields = ["user", "request"]
    date_hierarchy = "created_at"


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "email_new_request",
        "email_decision",
        "email_cancellation",
        "email_reminder",
        "digest_frequency",
    ]
    list_filter = ["digest_frequency"]
    search_fields = ["user__email"]
    autocomplete_fields = ["user"]
