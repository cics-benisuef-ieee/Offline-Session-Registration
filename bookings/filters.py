import uuid

from django.db.models import Q
from django.utils.dateparse import parse_date

from .models import SessionRequest

FILTER_KEYS = ("status", "place", "unit", "date_from", "date_to", "q")


def filter_requests(queryset, params, *, is_reviewer):
    """Apply the shared register filters used by the list, report, and CSV views."""
    status = params.get("status", "")
    place_id = params.get("place", "")
    unit_id = params.get("unit", "")
    date_from = params.get("date_from", "")
    date_to = params.get("date_to", "")
    query = params.get("q", "").strip()

    if status in SessionRequest.Status.values:
        queryset = queryset.filter(status=status)
    if place_id.isdigit():
        queryset = queryset.filter(place_id=place_id)
    if is_reviewer and unit_id.isdigit():
        queryset = queryset.filter(requesting_unit_id=unit_id)

    parsed_date_from = parse_date(date_from) if date_from else None
    parsed_date_to = parse_date(date_to) if date_to else None
    if parsed_date_from:
        queryset = queryset.filter(start_at__date__gte=parsed_date_from)
    if parsed_date_to:
        queryset = queryset.filter(start_at__date__lte=parsed_date_to)
    if query:
        queryset = queryset.filter(Q(title__icontains=query) | Q(purpose__icontains=query))

    return queryset


def filter_context(params, *, is_reviewer):
    """Return the normalized filter values the templates render back to the user."""
    status = params.get("status", "")
    return {
        "selected_status": status if status in SessionRequest.Status.values else "",
        "selected_place": params.get("place", ""),
        "selected_unit": params.get("unit", "") if is_reviewer else "",
        "date_from": params.get("date_from", ""),
        "date_to": params.get("date_to", ""),
        "query": params.get("q", "").strip(),
    }


def filter_history(queryset, params):
    """Apply the audit-log filters used by the status history viewer."""
    actor_id = params.get("actor", "")
    status = params.get("status", "")
    date_from = params.get("date_from", "")
    date_to = params.get("date_to", "")
    query = params.get("q", "").strip()

    if actor_id.isdigit():
        queryset = queryset.filter(changed_by_id=actor_id)
    if status in SessionRequest.Status.values:
        queryset = queryset.filter(to_status=status)

    parsed_date_from = parse_date(date_from) if date_from else None
    parsed_date_to = parse_date(date_to) if date_to else None
    if parsed_date_from:
        queryset = queryset.filter(created_at__date__gte=parsed_date_from)
    if parsed_date_to:
        queryset = queryset.filter(created_at__date__lte=parsed_date_to)

    if query:
        matches = Q(request__title__icontains=query)
        try:
            matches |= Q(request_id=uuid.UUID(query))
        except (ValueError, AttributeError):
            pass
        queryset = queryset.filter(matches)

    return queryset


def history_filter_context(params):
    status = params.get("status", "")
    return {
        "selected_actor": params.get("actor", ""),
        "selected_status": status if status in SessionRequest.Status.values else "",
        "date_from": params.get("date_from", ""),
        "date_to": params.get("date_to", ""),
        "query": params.get("q", "").strip(),
    }
