from collections import defaultdict
from datetime import datetime, time, timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone

from places.models import PlaceClosure

from .models import SessionRequest

SLOT_MINUTES = 30
WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def status_counts(queryset):
    return queryset.aggregate(
        total=Count("pk"),
        pending=Count("pk", filter=Q(status=SessionRequest.Status.PENDING)),
        approved=Count("pk", filter=Q(status=SessionRequest.Status.APPROVED)),
        rejected=Count("pk", filter=Q(status=SessionRequest.Status.REJECTED)),
        cancelled=Count("pk", filter=Q(status=SessionRequest.Status.CANCELLED)),
    )


def _per_unit(queryset):
    return list(
        queryset.values("requesting_unit__name")
        .annotate(
            total=Count("pk"),
            pending=Count("pk", filter=Q(status=SessionRequest.Status.PENDING)),
            approved=Count("pk", filter=Q(status=SessionRequest.Status.APPROVED)),
            rejected=Count("pk", filter=Q(status=SessionRequest.Status.REJECTED)),
            cancelled=Count("pk", filter=Q(status=SessionRequest.Status.CANCELLED)),
        )
        .order_by("-total", "requesting_unit__name")
    )


def _per_place(queryset):
    rows = list(
        queryset.values("place__name")
        .annotate(
            total=Count("pk"),
            approved=Count("pk", filter=Q(status=SessionRequest.Status.APPROVED)),
        )
        .order_by("-total", "place__name")
    )
    booked_minutes = {row["place__name"]: 0 for row in rows}
    approved_rows = queryset.filter(status=SessionRequest.Status.APPROVED).values_list(
        "place__name", "start_at", "end_at"
    )
    for place_name, start_at, end_at in approved_rows:
        booked_minutes[place_name] = booked_minutes.get(place_name, 0) + int(
            (end_at - start_at).total_seconds() // 60
        )
    for row in rows:
        row["booked_minutes"] = booked_minutes.get(row["place__name"], 0)
    return rows


def _monthly(queryset):
    return list(
        queryset.annotate(month=TruncMonth("start_at"))
        .values("month")
        .annotate(
            total=Count("pk"),
            pending=Count("pk", filter=Q(status=SessionRequest.Status.PENDING)),
            approved=Count("pk", filter=Q(status=SessionRequest.Status.APPROVED)),
            rejected=Count("pk", filter=Q(status=SessionRequest.Status.REJECTED)),
            cancelled=Count("pk", filter=Q(status=SessionRequest.Status.CANCELLED)),
        )
        .order_by("month")
    )


def _review_stats(queryset):
    decided = queryset.filter(
        status__in=[SessionRequest.Status.APPROVED, SessionRequest.Status.REJECTED]
    ).count()
    approved = queryset.filter(status=SessionRequest.Status.APPROVED).count()
    approval_rate = round((approved / decided) * 100) if decided else 0

    pairs = queryset.filter(reviewed_at__isnull=False).values_list(
        "created_at", "reviewed_at"
    )
    durations = [
        (reviewed_at - created_at).total_seconds()
        for created_at, reviewed_at in pairs
        if created_at and reviewed_at
    ]
    avg_review_hours = (
        round(sum(durations) / len(durations) / 3600, 1) if durations else None
    )
    return approval_rate, avg_review_hours


def build_report_context(queryset):
    """Build the counts, breakdowns, and chart payload for the reports page."""
    counts = status_counts(queryset)
    per_unit = _per_unit(queryset)
    per_place = _per_place(queryset)
    monthly = _monthly(queryset)
    approval_rate, avg_review_hours = _review_stats(queryset)

    charts = {
        "status": {
            "labels": ["Pending", "Approved", "Rejected", "Cancelled"],
            "data": [
                counts["pending"],
                counts["approved"],
                counts["rejected"],
                counts["cancelled"],
            ],
        },
        "units": {
            "labels": [row["requesting_unit__name"] for row in per_unit],
            "data": [row["total"] for row in per_unit],
        },
        "monthly": {
            "labels": [
                row["month"].strftime("%b %Y") if row["month"] else "" for row in monthly
            ],
            "total": [row["total"] for row in monthly],
            "approved": [row["approved"] for row in monthly],
        },
    }

    return {
        "counts": counts,
        "per_unit": per_unit,
        "per_place": per_place,
        "monthly": monthly,
        "approval_rate": approval_rate,
        "avg_review_hours": avg_review_hours,
        "charts": charts,
    }


def _minutes_between(start, end):
    return max(0, int((end - start).total_seconds() // 60))


def _hour_open_minutes(place, hour):
    """Return how many minutes of ``hour`` fall inside the place's open window."""
    open_at = place.opening_time.hour * 60 + place.opening_time.minute
    close_at = place.closing_time.hour * 60 + place.closing_time.minute
    hour_start = hour * 60
    return max(0, min(close_at, hour_start + 60) - max(open_at, hour_start))


def _open_hours(place):
    return [hour for hour in range(24) if _hour_open_minutes(place, hour) > 0]


def _heat_level(percent):
    if percent <= 0:
        return 0
    if percent < 25:
        return 1
    if percent < 50:
        return 2
    if percent < 75:
        return 3
    if percent < 100:
        return 4
    return 5


def _closure_ranges(place_ids, date_from, date_to):
    ranges = defaultdict(list)
    closures = PlaceClosure.objects.filter(
        place_id__in=place_ids,
        start_date__lte=date_to,
        end_date__gte=date_from,
    ).values_list("place_id", "start_date", "end_date")
    for place_id, start_date, end_date in closures:
        ranges[place_id].append((start_date, end_date))
    return ranges


def _is_closed(closure_ranges, place_id, on_date):
    return any(
        start_date <= on_date <= end_date
        for start_date, end_date in closure_ranges.get(place_id, ())
    )


def _dates_between(date_from, date_to):
    current = date_from
    while current <= date_to:
        yield current
        current += timedelta(days=1)


def build_utilization_heatmap(*, places, date_from, date_to):
    """Build a per-place weekday × hour utilization grid for approved sessions.

    Denominators are the place's open minutes for each weekday/hour in the range,
    excluding closure days. Booked minutes are split into 30-minute slots from a
    single approved-session queryset, then capped so percentages never exceed 100.
    """
    places = list(places)
    place_ids = [place.pk for place in places]
    if not places or date_from is None or date_to is None or date_from > date_to:
        return {
            "date_from": date_from,
            "date_to": date_to,
            "places": [],
            "booked_minutes": 0,
            "available_minutes": 0,
            "percent": 0,
        }

    tz = timezone.get_current_timezone()
    range_start = datetime.combine(date_from, time.min, tzinfo=tz)
    range_end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz)

    closures = _closure_ranges(place_ids, date_from, date_to)
    sessions_by_place = defaultdict(list)
    approved_sessions = (
        SessionRequest.objects.filter(
            place_id__in=place_ids,
            status=SessionRequest.Status.APPROVED,
            start_at__lt=range_end,
            end_at__gt=range_start,
        )
        .values_list("place_id", "start_at", "end_at")
        .iterator()
    )
    for place_id, start_at, end_at in approved_sessions:
        sessions_by_place[place_id].append((start_at, end_at))

    results = []
    total_booked = 0
    total_available = 0

    for place in places:
        hours = _open_hours(place)
        available = defaultdict(int)
        booked = defaultdict(int)

        for on_date in _dates_between(date_from, date_to):
            if _is_closed(closures, place.pk, on_date):
                continue
            weekday = on_date.weekday()
            for hour in hours:
                available[(weekday, hour)] += _hour_open_minutes(place, hour)

        for start_at, end_at in sessions_by_place.get(place.pk, ()):
            slot_start = start_at
            while slot_start < end_at:
                slot_end = slot_start + timedelta(minutes=SLOT_MINUTES)
                local_start = timezone.localtime(slot_start)
                local_end = timezone.localtime(slot_end)
                on_date = local_start.date()
                if (
                    date_from <= on_date <= date_to
                    and not _is_closed(closures, place.pk, on_date)
                    and local_start.date() == local_end.date()
                ):
                    hour = local_start.hour
                    booked[(on_date.weekday(), hour)] += _minutes_between(
                        slot_start, slot_end
                    )
                slot_start = slot_end

        rows = []
        place_booked = 0
        place_available = 0
        for weekday in range(7):
            cells = []
            for hour in hours:
                available_minutes = available.get((weekday, hour), 0)
                booked_minutes = min(
                    booked.get((weekday, hour), 0), available_minutes
                )
                percent = (
                    round(booked_minutes / available_minutes * 100)
                    if available_minutes
                    else 0
                )
                cells.append(
                    {
                        "hour": hour,
                        "label": f"{hour:02d}:00",
                        "booked": booked_minutes,
                        "available": available_minutes,
                        "percent": percent,
                        "level": _heat_level(percent),
                    }
                )
                place_booked += booked_minutes
                place_available += available_minutes
            rows.append(
                {
                    "weekday": weekday,
                    "label": WEEKDAY_LABELS[weekday],
                    "cells": cells,
                }
            )

        place_percent = (
            round(place_booked / place_available * 100) if place_available else 0
        )
        total_booked += place_booked
        total_available += place_available
        results.append(
            {
                "place": place,
                "hours": hours,
                "rows": rows,
                "booked_minutes": place_booked,
                "available_minutes": place_available,
                "percent": place_percent,
            }
        )

    total_percent = round(total_booked / total_available * 100) if total_available else 0
    return {
        "date_from": date_from,
        "date_to": date_to,
        "places": results,
        "booked_minutes": total_booked,
        "available_minutes": total_available,
        "percent": total_percent,
    }
