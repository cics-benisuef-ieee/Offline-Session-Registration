from django.contrib import admin

from .models import Notification, NotificationPreference, PushSubscription


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["title", "user", "kind", "read_at", "created_at"]
    list_filter = ["kind", "read_at"]
    search_fields = ["title", "body", "user__email"]
    autocomplete_fields = ["user", "request"]
    date_hierarchy = "created_at"


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ["user", "digest_frequency"]
    list_filter = ["digest_frequency"]
    search_fields = ["user__email"]
    autocomplete_fields = ["user"]


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ["user", "created_at", "last_used_at"]
    search_fields = ["user__email"]
    autocomplete_fields = ["user"]
    readonly_fields = [
        "user",
        "endpoint",
        "p256dh",
        "auth",
        "user_agent",
        "created_at",
        "last_used_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
