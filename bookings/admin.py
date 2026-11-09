from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.shortcuts import render

from .models import BookingSeries, RequestStatusHistory, SessionRequest
from .services import delete_session_request, restore_session_request


class DeletionFilter(admin.SimpleListFilter):
    title = "deletion state"
    parameter_name = "deleted"

    def lookups(self, request, model_admin):
        return (
            ("active", "Active"),
            ("deleted", "Deleted"),
        )

    def queryset(self, request, queryset):
        if self.value() == "active":
            return queryset.filter(deleted_at__isnull=True)
        if self.value() == "deleted":
            return queryset.filter(deleted_at__isnull=False)
        return queryset


class SoftDeleteForm(forms.Form):
    reason = forms.CharField(
        label="Deletion reason",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


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
        "series",
        "deleted_at",
    ]
    list_filter = ["status", "requesting_unit", "place", DeletionFilter]
    search_fields = ["title", "purpose", "requesting_unit__name", "place__name"]
    date_hierarchy = "start_at"
    autocomplete_fields = [
        "requesting_unit",
        "reviewer_unit",
        "place",
        "submitted_by",
        "reviewer",
        "series",
    ]
    readonly_fields = [
        "id",
        "status",
        "reviewer",
        "reviewed_at",
        "decision_reason",
        "deleted_at",
        "deleted_by",
        "deletion_reason",
        "series",
        "series_index",
        "created_at",
        "updated_at",
    ]
    inlines = [RequestStatusHistoryInline]
    actions = ["soft_delete_selected", "restore_selected"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return (
            SessionRequest.all_objects.select_related(
                "requesting_unit",
                "reviewer_unit",
                "place",
                "submitted_by",
                "reviewer",
                "series",
            )
        )

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            actions.pop("soft_delete_selected", None)
            actions.pop("restore_selected", None)
        return actions

    @admin.action(description="Soft delete selected requests")
    def soft_delete_selected(self, request, queryset):
        if "apply" in request.POST:
            form = SoftDeleteForm(request.POST)
            if form.is_valid():
                reason = form.cleaned_data["reason"]
                deleted = 0
                failed = 0
                for request_id in queryset.values_list("pk", flat=True):
                    try:
                        delete_session_request(
                            request_id=request_id,
                            actor=request.user,
                            reason=reason,
                        )
                    except ValidationError:
                        failed += 1
                    else:
                        deleted += 1
                self.message_user(
                    request,
                    f"{deleted} request(s) deleted."
                    + (f" {failed} request(s) could not be deleted." if failed else ""),
                )
                return None
        else:
            form = SoftDeleteForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Soft delete selected requests",
            "form": form,
            "queryset": queryset,
            "opts": self.model._meta,
            "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
        }
        return render(request, "admin/bookings/soft_delete.html", context)

    @admin.action(description="Restore selected requests")
    def restore_selected(self, request, queryset):
        restored = 0
        failed = 0
        for request_id in queryset.filter(deleted_at__isnull=False).values_list(
            "pk", flat=True
        ):
            try:
                restore_session_request(
                    request_id=request_id,
                    actor=request.user,
                )
            except ValidationError:
                failed += 1
            else:
                restored += 1
        self.message_user(
            request,
            f"{restored} request(s) restored."
            + (f" {failed} request(s) could not be restored." if failed else ""),
        )


@admin.register(BookingSeries)
class BookingSeriesAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "requesting_unit",
        "place",
        "frequency",
        "start_date",
        "until_date",
        "submitted_by",
    ]
    list_filter = ["frequency", "requesting_unit", "place"]
    search_fields = ["title", "purpose", "requesting_unit__name", "place__name"]
    date_hierarchy = "start_date"
    autocomplete_fields = ["requesting_unit", "place", "submitted_by"]
    readonly_fields = ["created_at"]

    def has_add_permission(self, request):
        return False
