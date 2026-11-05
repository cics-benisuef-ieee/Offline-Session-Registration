from django.contrib import admin

from .models import RequestStatusHistory, SessionRequest


class RequestStatusHistoryInline(admin.TabularInline):
    model = RequestStatusHistory
    extra = 0
    readonly_fields = [
        "from_status",
        "to_status",
        "changed_by",
        "reason",
        "created_at",
    ]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SessionRequest)
class SessionRequestAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "requesting_unit",
        "place",
        "start_at",
        "end_at",
        "status",
        "submitted_by",
    ]
    list_filter = ["status", "requesting_unit", "place"]
    search_fields = ["title", "purpose", "requesting_unit__name", "place__name"]
    date_hierarchy = "start_at"
    autocomplete_fields = [
        "requesting_unit",
        "reviewer_unit",
        "place",
        "submitted_by",
        "reviewer",
    ]
    readonly_fields = [
        "id",
        "status",
        "reviewer",
        "reviewed_at",
        "decision_reason",
        "created_at",
        "updated_at",
    ]
    inlines = [RequestStatusHistoryInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "requesting_unit", "reviewer_unit", "place", "submitted_by", "reviewer"
        )
