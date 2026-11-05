from django.contrib import admin

from .models import Place, PlaceClosure


class PlaceClosureInline(admin.TabularInline):
    model = PlaceClosure
    extra = 0


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ["name", "building", "room", "opening_time", "closing_time", "is_active"]
    list_filter = ["is_active", "building"]
    search_fields = ["name", "building", "room"]
    inlines = [PlaceClosureInline]


@admin.register(PlaceClosure)
class PlaceClosureAdmin(admin.ModelAdmin):
    list_display = ["place", "start_date", "end_date", "reason"]
    list_filter = ["place"]
    search_fields = ["place__name", "reason"]
    date_hierarchy = "start_date"
