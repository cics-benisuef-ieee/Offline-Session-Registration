from django.contrib import admin

from .models import Unit


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "parent", "is_active"]
    list_filter = ["kind", "is_active", "parent"]
    search_fields = ["name", "slug"]
    autocomplete_fields = ["parent"]
    prepopulated_fields = {"slug": ("name",)}
