import csv
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_POST

from places.models import Place
from units.models import Unit

from .forms import CancelForm, RejectForm, SessionRequestForm
from .models import SessionRequest
from .services import (
    approve_session_request,
    can_cancel_request,
    can_view_request,
    cancel_session_request,
    get_available_slots,
    get_conflicting_requests,
    reject_session_request,
    visible_requests_for,
)


def _status_counts(queryset):
    """Return total and per-status counts in a single database round trip."""
    return queryset.aggregate(
        total=Count("pk"),
        pending=Count("pk", filter=Q(status=SessionRequest.Status.PENDING)),
        approved=Count("pk", filter=Q(status=SessionRequest.Status.APPROVED)),
        rejected=Count("pk", filter=Q(status=SessionRequest.Status.REJECTED)),
        cancelled=Count("pk", filter=Q(status=SessionRequest.Status.CANCELLED)),
    )


def home(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return redirect("login")


@login_required
def dashboard(request):
    visible = visible_requests_for(request.user)
    now = timezone.now()
    counts = _status_counts(visible)
    recent = visible.select_related("requesting_unit", "place").order_by(
        "-created_at"
    )[:8]
    upcoming = (
        visible.filter(
            status=SessionRequest.Status.APPROVED,
            start_at__gte=now,
        )
        .select_related("place")
        .order_by("start_at")[:5]
    )
    return render(
        request,
        "bookings/dashboard.html",
        {
            "counts": counts,
            "recent": recent,
            "upcoming": upcoming,
            "is_reviewer": request.user.is_superuser or request.user.is_officer,
        },
    )


@login_required
def request_list(request):
    visible = visible_requests_for(request.user).select_related(
        "requesting_unit", "place"
    )

    status = request.GET.get("status", "")
    place_id = request.GET.get("place", "")
    unit_id = request.GET.get("unit", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    query = request.GET.get("q", "").strip()
    is_reviewer = request.user.is_superuser or request.user.is_officer

    if status in SessionRequest.Status.values:
        visible = visible.filter(status=status)
    if place_id.isdigit():
        visible = visible.filter(place_id=place_id)
    if is_reviewer and unit_id.isdigit():
        visible = visible.filter(requesting_unit_id=unit_id)
    parsed_date_from = parse_date(date_from) if date_from else None
    parsed_date_to = parse_date(date_to) if date_to else None
    if parsed_date_from:
        visible = visible.filter(start_at__date__gte=parsed_date_from)
    if parsed_date_to:
        visible = visible.filter(start_at__date__lte=parsed_date_to)
    if query:
        visible = visible.filter(Q(title__icontains=query) | Q(purpose__icontains=query))

    paginator = Paginator(visible, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    querystring = request.GET.copy()
    querystring.pop("page", None)

    return render(
        request,
        "bookings/request_list.html",
        {
            "page_obj": page_obj,
            "statuses": SessionRequest.Status.choices,
            "selected_status": status,
            "places": Place.objects.filter(is_active=True),
            "selected_place": place_id,
            "units": Unit.objects.filter(is_active=True) if is_reviewer else Unit.objects.none(),
            "selected_unit": unit_id,
            "date_from": date_from,
            "date_to": date_to,
            "query": query,
            "is_reviewer": is_reviewer,
            "querystring": querystring.urlencode(),
        },
    )


@login_required
def request_detail(request, pk):
    session_request = get_object_or_404(
        visible_requests_for(request.user).select_related(
            "requesting_unit",
            "reviewer_unit",
            "place",
            "submitted_by",
            "reviewer",
        ),
        pk=pk,
    )
    if not can_view_request(request.user, session_request):
        raise PermissionDenied

    conflicts = None
    if request.user.is_superuser or request.user.is_officer:
        conflicts = get_conflicting_requests(session_request).select_related(
            "requesting_unit", "place"
        )

    return render(
        request,
        "bookings/request_detail.html",
        {
            "session_request": session_request,
            "history": session_request.history.select_related("changed_by"),
            "conflicts": conflicts,
            "can_review": request.user.is_superuser or request.user.is_officer,
            "can_cancel": can_cancel_request(request.user, session_request),
            "cancel_form": CancelForm(),
        },
    )


@login_required
def request_create(request):
    form = SessionRequestForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            session_request = form.save(request.user)
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Session request submitted for review.")
            return redirect("request_detail", pk=session_request.pk)

    return render(request, "bookings/request_form.html", {"form": form})


@login_required
@require_POST
def request_cancel(request, pk):
    session_request = get_object_or_404(
        visible_requests_for(request.user), pk=pk
    )
    if not can_cancel_request(request.user, session_request):
        raise PermissionDenied

    form = CancelForm(request.POST)
    if form.is_valid():
        try:
            cancel_session_request(
                request_id=session_request.pk,
                actor=request.user,
                reason=form.cleaned_data["reason"],
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Request cancelled.")
    else:
        messages.error(request, "The cancellation form is invalid.")

    return redirect("request_detail", pk=session_request.pk)


@login_required
def review_queue(request):
    if not (request.user.is_superuser or request.user.is_officer):
        raise PermissionDenied

    pending = (
        visible_requests_for(request.user)
        .filter(status=SessionRequest.Status.PENDING)
        .select_related("requesting_unit", "place", "submitted_by")
        .order_by("start_at")
    )
    return render(request, "bookings/review_queue.html", {"pending": pending})


@login_required
def request_review(request, pk):
    if not (request.user.is_superuser or request.user.is_officer):
        raise PermissionDenied

    session_request = get_object_or_404(
        SessionRequest.objects.select_related(
            "requesting_unit", "place", "submitted_by"
        ),
        pk=pk,
    )
    if session_request.status != SessionRequest.Status.PENDING:
        messages.info(request, "This request has already been reviewed.")
        return redirect("request_detail", pk=session_request.pk)

    reject_form = RejectForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "approve":
            try:
                approve_session_request(
                    request_id=session_request.pk,
                    reviewer=request.user,
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                messages.success(request, "Request approved.")
                return redirect("request_detail", pk=session_request.pk)
        elif action == "reject":
            reject_form = RejectForm(request.POST)
            if reject_form.is_valid():
                try:
                    reject_session_request(
                        request_id=session_request.pk,
                        reviewer=request.user,
                        reason=reject_form.cleaned_data["reason"],
                    )
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                else:
                    messages.success(request, "Request rejected.")
                    return redirect("request_detail", pk=session_request.pk)
        else:
            messages.error(request, "Choose approve or reject.")

    return render(
        request,
        "bookings/request_review.html",
        {
            "session_request": session_request,
            "conflicts": get_conflicting_requests(session_request).select_related(
                "requesting_unit", "place"
            ),
            "reject_form": reject_form,
        },
    )


@login_required
def availability(request):
    place_id = request.GET.get("place", "")
    raw_date = request.GET.get("date", "")
    if not place_id.isdigit() or not raw_date:
        return render(
            request,
            "bookings/partials/availability.html",
            {"error": "Choose a place and date to see available slots."},
        )

    place = get_object_or_404(Place, pk=place_id)
    on_date = parse_date(raw_date)
    if on_date is None:
        return render(
            request,
            "bookings/partials/availability.html",
            {"error": "Enter a valid date."},
        )

    slots = get_available_slots(place, on_date)
    if request.GET.get("format") == "json":
        return JsonResponse(
            {
                "place_id": place.pk,
                "date": on_date.isoformat(),
                "slots": slots,
            }
        )

    return render(
        request,
        "bookings/partials/availability.html",
        {
            "place": place,
            "date": on_date,
            "slots": slots,
            "is_closed": not slots,
        },
    )


@login_required
def calendar_view(request):
    return render(
        request,
        "bookings/calendar.html",
        {"places": Place.objects.filter(is_active=True)},
    )


@login_required
def calendar_events(request):
    def parse_calendar_value(value):
        if not value:
            return None
        try:
            return parse_datetime(value)
        except ValueError:
            return None

    start = parse_calendar_value(request.GET.get("start"))
    end = parse_calendar_value(request.GET.get("end"))
    if start is None:
        start = timezone.now() - timedelta(days=30)
    if end is None:
        end = timezone.now() + timedelta(days=90)
    if timezone.is_naive(start):
        start = timezone.make_aware(start)
    if timezone.is_naive(end):
        end = timezone.make_aware(end)

    events = []
    requests = (
        visible_requests_for(request.user)
        .filter(
            status=SessionRequest.Status.APPROVED,
            start_at__lt=end,
            end_at__gt=start,
        )
        .select_related("place")
        .only("id", "title", "start_at", "end_at", "place__name")
    )
    place_id = request.GET.get("place", "")
    if place_id.isdigit():
        requests = requests.filter(place_id=place_id)
    for session_request in requests:
        events.append(
            {
                "id": str(session_request.pk),
                "title": f"{session_request.title} — {session_request.place}",
                "start": session_request.start_at.isoformat(),
                "end": session_request.end_at.isoformat(),
                "url": reverse("request_detail", args=[session_request.pk]),
                "backgroundColor": "#00629B",
                "borderColor": "#002855",
            }
        )
    return JsonResponse(events, safe=False)


@login_required
def reports(request):
    if not (request.user.is_superuser or request.user.is_officer):
        raise PermissionDenied

    visible = visible_requests_for(request.user)
    status = request.GET.get("status", "")
    place_id = request.GET.get("place", "")
    unit_id = request.GET.get("unit", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    query = request.GET.get("q", "").strip()

    if status in SessionRequest.Status.values:
        visible = visible.filter(status=status)
    if place_id.isdigit():
        visible = visible.filter(place_id=place_id)
    if unit_id.isdigit():
        visible = visible.filter(requesting_unit_id=unit_id)
    parsed_date_from = parse_date(date_from) if date_from else None
    parsed_date_to = parse_date(date_to) if date_to else None
    if parsed_date_from:
        visible = visible.filter(start_at__date__gte=parsed_date_from)
    if parsed_date_to:
        visible = visible.filter(start_at__date__lte=parsed_date_to)
    if query:
        visible = visible.filter(Q(title__icontains=query) | Q(purpose__icontains=query))

    counts = _status_counts(visible)
    querystring = request.GET.copy()
    querystring.pop("page", None)

    return render(
        request,
        "bookings/reports.html",
        {
            "counts": counts,
            "statuses": SessionRequest.Status.choices,
            "selected_status": status,
            "places": Place.objects.filter(is_active=True),
            "selected_place": place_id,
            "units": Unit.objects.filter(is_active=True),
            "selected_unit": unit_id,
            "date_from": date_from,
            "date_to": date_to,
            "query": query,
            "querystring": querystring.urlencode(),
        },
    )


@login_required
def export_requests_csv(request):
    if not (request.user.is_superuser or request.user.is_officer):
        raise PermissionDenied

    visible = visible_requests_for(request.user).select_related(
        "requesting_unit", "place", "submitted_by", "reviewer"
    )

    status = request.GET.get("status", "")
    place_id = request.GET.get("place", "")
    unit_id = request.GET.get("unit", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    query = request.GET.get("q", "").strip()

    if status in SessionRequest.Status.values:
        visible = visible.filter(status=status)
    if place_id.isdigit():
        visible = visible.filter(place_id=place_id)
    if unit_id.isdigit():
        visible = visible.filter(requesting_unit_id=unit_id)
    parsed_date_from = parse_date(date_from) if date_from else None
    parsed_date_to = parse_date(date_to) if date_to else None
    if parsed_date_from:
        visible = visible.filter(start_at__date__gte=parsed_date_from)
    if parsed_date_to:
        visible = visible.filter(start_at__date__lte=parsed_date_to)
    if query:
        visible = visible.filter(Q(title__icontains=query) | Q(purpose__icontains=query))

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="session-requests.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(
        [
            "ID",
            "Title",
            "Unit",
            "Place",
            "Start",
            "End",
            "Status",
            "Submitted by",
            "Reviewed by",
            "Reviewed at",
            "Decision reason",
        ]
    )
    for session_request in visible:
        writer.writerow(
            [
                session_request.pk,
                session_request.title,
                session_request.requesting_unit,
                session_request.place,
                timezone.localtime(session_request.start_at).strftime("%Y-%m-%d %H:%M"),
                timezone.localtime(session_request.end_at).strftime("%Y-%m-%d %H:%M"),
                session_request.get_status_display(),
                session_request.submitted_by,
                session_request.reviewer or "",
                timezone.localtime(session_request.reviewed_at).strftime("%Y-%m-%d %H:%M")
                if session_request.reviewed_at
                else "",
                session_request.decision_reason,
            ]
        )
    return response
