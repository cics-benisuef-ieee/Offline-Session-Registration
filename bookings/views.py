import csv
import json
from datetime import timedelta
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Font

from accounts.models import User
from places.models import Place
from units.models import Unit

from .filters import (
    filter_context,
    filter_history,
    filter_requests,
    history_filter_context,
)
from .forms import (
    BookingSeriesForm,
    CancelForm,
    RejectForm,
    SessionRequestForm,
)
from .models import CalendarFeed, RequestStatusHistory, SessionRequest
from .reports import build_report_context, build_utilization_heatmap, status_counts
from .series import (
    approve_series,
    can_manage_series,
    cancel_series,
    create_booking_series,
    parse_series_time,
    reject_series,
    reschedule_series_future,
    visible_series_for,
)
from .services import (
    approve_session_request,
    build_ics_calendar,
    bulk_decide,
    can_cancel_request,
    can_reschedule_request,
    can_view_request,
    cancel_session_request,
    clone_initial,
    delete_session_request,
    find_next_available_slot,
    find_next_available_slots,
    get_available_slots,
    get_calendar_feed,
    get_conflicting_requests,
    google_calendar_url,
    reject_session_request,
    reschedule_session_request,
    rotate_calendar_feed,
    visible_requests_for,
)


def home(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return redirect("login")


@login_required
def dashboard(request):
    visible = visible_requests_for(request.user)
    now = timezone.now()
    counts = status_counts(visible)
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
    is_reviewer = request.user.is_superuser or request.user.is_officer
    visible = filter_requests(visible, request.GET, is_reviewer=is_reviewer)

    paginator = Paginator(visible, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    querystring = request.GET.copy()
    querystring.pop("page", None)

    context = {
        "page_obj": page_obj,
        "statuses": SessionRequest.Status.choices,
        "places": Place.objects.filter(is_active=True),
        "units": Unit.objects.filter(is_active=True)
        if is_reviewer
        else Unit.objects.none(),
        "is_reviewer": is_reviewer,
        "querystring": querystring.urlencode(),
    }
    context.update(filter_context(request.GET, is_reviewer=is_reviewer))
    return render(request, "bookings/request_list.html", context)


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

    calendar_url = ""
    if session_request.status == SessionRequest.Status.APPROVED:
        calendar_url = google_calendar_url(
            session_request,
            absolute_url=request.build_absolute_uri(
                reverse("request_detail", args=[session_request.pk])
            ),
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
            "google_calendar_url": calendar_url,
        },
    )


@login_required
def request_create(request):
    initial = None
    source_id = request.GET.get("from")
    if source_id:
        try:
            source = get_object_or_404(
                visible_requests_for(request.user), pk=source_id
            )
        except (ValidationError, ValueError):
            raise Http404 from None
        initial = clone_initial(request.user, source)

    form = SessionRequestForm(request.POST or None, user=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            session_request = form.save(request.user)
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Session request submitted for review.")
            return redirect("request_detail", pk=session_request.pk)

    return render(request, "bookings/request_form.html", {"form": form})


def _report_series_result(request, result, verb):
    if result["succeeded"]:
        messages.success(
            request,
            f"{len(result['succeeded'])} occurrence(s) {verb}.",
        )
    if result["failed"]:
        details = "; ".join(
            f"{item['id']}: {item['error']}" for item in result["failed"]
        )
        messages.warning(
            request,
            f"{len(result['failed'])} occurrence(s) could not be updated. {details}",
        )


@login_required
def series_create(request):
    form = BookingSeriesForm(request.POST or None, user=request.user)
    occurrences = None

    if request.method == "POST" and form.is_valid():
        occurrences = form.cleaned_data["occurrences"]
        if request.POST.get("action") == "create":
            selected = [
                value
                for value in request.POST.getlist("selected_dates")
                if value
            ]
            try:
                series = create_booking_series(
                    user=request.user,
                    requesting_unit=form.cleaned_data.get("requesting_unit"),
                    place=form.cleaned_data["place"],
                    title=form.cleaned_data["title"],
                    purpose=form.cleaned_data["purpose"],
                    frequency=form.cleaned_data["frequency"],
                    weekday=form.cleaned_data["weekday"],
                    start_time=form.cleaned_data["start_time"],
                    duration_minutes=form.cleaned_data["duration_minutes"],
                    start_date=form.cleaned_data["start_date"],
                    until_date=form.cleaned_data["until_date"],
                    occurrence_count=form.cleaned_data["occurrence_count"],
                    selected_dates=selected,
                )
            except (PermissionDenied, ValidationError) as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    "Recurring series submitted for review.",
                )
                return redirect("series_detail", pk=series.pk)

    return render(
        request,
        "bookings/series_form.html",
        {"form": form, "occurrences": occurrences},
    )


@login_required
@require_POST
def series_preview(request):
    form = BookingSeriesForm(request.POST, user=request.user)
    occurrences = form.cleaned_data["occurrences"] if form.is_valid() else None
    return render(
        request,
        "bookings/partials/series_preview.html",
        {"form": form, "occurrences": occurrences},
    )


@login_required
def series_detail(request, pk):
    series = get_object_or_404(
        visible_series_for(request.user).select_related(
            "requesting_unit",
            "place",
            "submitted_by",
        ),
        pk=pk,
    )
    occurrences = series.occurrences.select_related(
        "requesting_unit",
        "place",
        "submitted_by",
    ).order_by("start_at")
    return render(
        request,
        "bookings/series_detail.html",
        {
            "series": series,
            "occurrences": occurrences,
            "can_manage": can_manage_series(request.user, series),
            "can_review": request.user.is_superuser or request.user.is_officer,
            "reject_form": RejectForm(),
            "cancel_form": CancelForm(),
        },
    )


@login_required
@require_POST
def series_approve(request, pk):
    if not (request.user.is_superuser or request.user.is_officer):
        raise PermissionDenied
    get_object_or_404(visible_series_for(request.user), pk=pk)
    result = approve_series(series_id=pk, reviewer=request.user)
    _report_series_result(request, result, "approved")
    return redirect("series_detail", pk=pk)


@login_required
@require_POST
def series_reject(request, pk):
    if not (request.user.is_superuser or request.user.is_officer):
        raise PermissionDenied
    get_object_or_404(visible_series_for(request.user), pk=pk)
    form = RejectForm(request.POST)
    if form.is_valid():
        result = reject_series(
            series_id=pk,
            reviewer=request.user,
            reason=form.cleaned_data["reason"],
        )
        _report_series_result(request, result, "rejected")
    else:
        messages.error(request, "A rejection reason is required.")
    return redirect("series_detail", pk=pk)


@login_required
@require_POST
def series_cancel(request, pk):
    get_object_or_404(visible_series_for(request.user), pk=pk)
    scope = request.POST.get("scope", "future")
    occurrence = request.POST.get("occurrence") or None
    reason = request.POST.get("reason", "")
    try:
        result = cancel_series(
            series_id=pk,
            actor=request.user,
            reason=reason,
            scope=scope,
            from_occurrence=occurrence,
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
    else:
        _report_series_result(request, result, "cancelled")
    return redirect("series_detail", pk=pk)


@login_required
@require_POST
def series_reschedule(request, pk):
    get_object_or_404(visible_series_for(request.user), pk=pk)
    occurrence = request.POST.get("occurrence") or None
    raw_weekday = request.POST.get("weekday", "")
    raw_time = request.POST.get("start_time", "")
    new_weekday = int(raw_weekday) if raw_weekday not in {"", None} else None
    try:
        new_start_time = parse_series_time(raw_time) if raw_time else None
        result = reschedule_series_future(
            series_id=pk,
            actor=request.user,
            from_occurrence=occurrence,
            new_start_time=new_start_time,
            new_weekday=new_weekday,
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
    else:
        _report_series_result(request, result, "rescheduled")
    return redirect("series_detail", pk=pk)


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
@require_POST
def request_delete(request, pk):
    if not request.user.is_superuser:
        raise PermissionDenied

    session_request = get_object_or_404(SessionRequest.all_objects, pk=pk)
    reason = request.POST.get("reason", "")
    try:
        delete_session_request(
            request_id=session_request.pk,
            actor=request.user,
            reason=reason,
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("request_detail", pk=session_request.pk)

    messages.success(request, "Session request deleted.")
    return redirect("request_list")


@login_required
@require_POST
def request_reschedule(request, pk):
    session_request = get_object_or_404(
        visible_requests_for(request.user), pk=pk
    )
    try:
        payload = json.loads(request.body or b"{}")
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    raw_start = payload.get("start")
    raw_end = payload.get("end")
    new_start = parse_datetime(raw_start) if raw_start else None
    new_end = parse_datetime(raw_end) if raw_end else None
    if new_start is None or new_end is None:
        return JsonResponse(
            {"error": "Provide both start and end timestamps."}, status=400
        )
    if timezone.is_naive(new_start):
        new_start = timezone.make_aware(new_start)
    if timezone.is_naive(new_end):
        new_end = timezone.make_aware(new_end)

    try:
        updated = reschedule_session_request(
            request_id=session_request.pk,
            actor=request.user,
            new_start=new_start,
            new_end=new_end,
        )
    except PermissionDenied:
        return JsonResponse(
            {"error": "You cannot reschedule this request."}, status=403
        )
    except ValidationError as exc:
        return JsonResponse({"error": "; ".join(exc.messages)}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "start": updated.start_at.isoformat(),
            "end": updated.end_at.isoformat(),
        }
    )


@login_required
def review_queue(request):
    if not (request.user.is_superuser or request.user.is_officer):
        raise PermissionDenied

    pending = (
        visible_requests_for(request.user)
        .filter(status=SessionRequest.Status.PENDING)
        .select_related("requesting_unit", "place", "submitted_by", "series")
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

    conflicts = get_conflicting_requests(session_request).select_related(
        "requesting_unit", "place"
    )
    suggested_slots = []
    if conflicts:
        suggested_slots = find_next_available_slots(
            place=session_request.place,
            duration_minutes=session_request.duration_minutes,
            after=session_request.start_at,
            limit=3,
        )

    return render(
        request,
        "bookings/request_review.html",
        {
            "session_request": session_request,
            "conflicts": conflicts,
            "suggested_slots": suggested_slots,
            "reject_form": reject_form,
        },
    )


@login_required
@require_POST
def request_bulk_review(request):
    if not (request.user.is_superuser or request.user.is_officer):
        raise PermissionDenied

    request_ids = request.POST.getlist("request_ids")
    action = request.POST.get("action", "")
    reason = request.POST.get("reason", "")

    if not request_ids:
        messages.error(request, "Select at least one request.")
        return redirect("review_queue")

    try:
        result = bulk_decide(
            request_ids=request_ids,
            action=action,
            reviewer=request.user,
            reason=reason,
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("review_queue")

    if result["succeeded"]:
        verb = "approved" if action == "approve" else "rejected"
        messages.success(
            request,
            f"{len(result['succeeded'])} request(s) {verb}.",
        )
    if result["failed"]:
        details = "; ".join(
            f"{item['id']}: {item['error']}" for item in result["failed"]
        )
        messages.warning(
            request,
            f"{len(result['failed'])} request(s) could not be updated. {details}",
        )
    return redirect("review_queue")


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
def availability_next(request):
    place_id = request.GET.get("place", "")
    raw_duration = request.GET.get("duration", "")
    raw_after = request.GET.get("after", "")

    if not place_id.isdigit() or not raw_duration.isdigit():
        return JsonResponse(
            {"error": "Choose a place and duration."},
            status=400,
        )

    place = get_object_or_404(Place, pk=place_id)
    after = parse_datetime(raw_after) if raw_after else None
    if after and timezone.is_naive(after):
        after = timezone.make_aware(after)

    try:
        slot = find_next_available_slot(
            place=place,
            duration_minutes=int(raw_duration),
            after=after,
        )
    except ValidationError as exc:
        return JsonResponse({"error": "; ".join(exc.messages)}, status=400)

    return JsonResponse(
        {
            "place_id": place.pk,
            "duration_minutes": int(raw_duration),
            "slot": slot,
        }
    )


@login_required
def calendar_view(request):
    feed = get_calendar_feed(request.user)
    feed_url = request.build_absolute_uri(
        reverse("calendar_feed", args=[feed.token])
    )
    return render(
        request,
        "bookings/calendar.html",
        {
            "places": Place.objects.filter(is_active=True),
            "is_reviewer": request.user.is_superuser or request.user.is_officer,
            "feed_url": feed_url,
            "feed_is_active": feed.is_active,
        },
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

    is_reviewer = request.user.is_superuser or request.user.is_officer
    statuses = [SessionRequest.Status.APPROVED]
    if is_reviewer and request.GET.get("include_pending") == "1":
        statuses.append(SessionRequest.Status.PENDING)

    events = []
    requests = (
        visible_requests_for(request.user)
        .filter(
            status__in=statuses,
            start_at__lt=end,
            end_at__gt=start,
        )
        .select_related("place")
        .only(
            "id",
            "title",
            "start_at",
            "end_at",
            "status",
            "requesting_unit_id",
            "place__name",
        )
    )
    place_id = request.GET.get("place", "")
    if place_id.isdigit():
        requests = requests.filter(place_id=place_id)
    for session_request in requests:
        approved = session_request.status == SessionRequest.Status.APPROVED
        events.append(
            {
                "id": str(session_request.pk),
                "title": f"{session_request.title} — {session_request.place}",
                "start": session_request.start_at.isoformat(),
                "end": session_request.end_at.isoformat(),
                "url": reverse("request_detail", args=[session_request.pk]),
                "backgroundColor": "#00629B" if approved else "#C8A24A",
                "borderColor": "#002855" if approved else "#8A6D1F",
                "classNames": ["event-approved" if approved else "event-pending"],
                "extendedProps": {
                    "status": session_request.status,
                    "can_reschedule": can_reschedule_request(
                        request.user, session_request
                    ),
                    "reschedule_url": reverse(
                        "request_reschedule", args=[session_request.pk]
                    ),
                },
            }
        )
    return JsonResponse(events, safe=False)


def calendar_feed(request, token):
    feed = (
        CalendarFeed.objects.select_related("user")
        .filter(token=token, is_active=True, user__is_active=True)
        .first()
    )
    if feed is None:
        raise Http404("Calendar feed not found.")

    approved = (
        visible_requests_for(feed.user)
        .filter(status=SessionRequest.Status.APPROVED)
        .select_related("place")
        .order_by("start_at")
    )
    payload = build_ics_calendar(approved, request=request)
    CalendarFeed.objects.filter(pk=feed.pk).update(last_accessed_at=timezone.now())

    response = HttpResponse(payload, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = 'inline; filename="sessions.ics"'
    return response


@login_required
@require_POST
def calendar_feed_rotate(request):
    rotate_calendar_feed(request.user)
    messages.success(request, "Calendar feed link rotated. The old link no longer works.")
    return redirect("calendar")


@login_required
def request_ics(request, pk):
    session_request = get_object_or_404(
        visible_requests_for(request.user).select_related("place"),
        pk=pk,
    )
    if session_request.status != SessionRequest.Status.APPROVED:
        raise Http404("Only approved sessions can be downloaded.")
    payload = build_ics_calendar([session_request], request=request)
    response = HttpResponse(payload, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="session-{session_request.pk}.ics"'
    )
    return response


@login_required
def reports(request):
    if not (request.user.is_superuser or request.user.is_officer):
        raise PermissionDenied

    visible = filter_requests(
        visible_requests_for(request.user), request.GET, is_reviewer=True
    )
    report = build_report_context(visible)
    querystring = request.GET.copy()
    querystring.pop("page", None)

    context = {
        **report,
        "statuses": SessionRequest.Status.choices,
        "places": Place.objects.filter(is_active=True),
        "units": Unit.objects.filter(is_active=True),
        "querystring": querystring.urlencode(),
    }
    context.update(filter_context(request.GET, is_reviewer=True))
    return render(request, "bookings/reports.html", context)


@login_required
def export_requests_csv(request):
    if not (request.user.is_superuser or request.user.is_officer):
        raise PermissionDenied

    visible = visible_requests_for(request.user).select_related(
        "requesting_unit", "place", "submitted_by", "reviewer"
    )
    visible = filter_requests(visible, request.GET, is_reviewer=True)

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


def _require_reviewer(user):
    if not (user.is_superuser or user.is_officer):
        raise PermissionDenied


def _xlsx_headers():
    return [
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


def _xlsx_row(session_request):
    return [
        str(session_request.pk),
        session_request.title,
        str(session_request.requesting_unit),
        str(session_request.place),
        timezone.localtime(session_request.start_at).strftime("%Y-%m-%d %H:%M"),
        timezone.localtime(session_request.end_at).strftime("%Y-%m-%d %H:%M"),
        session_request.get_status_display(),
        str(session_request.submitted_by),
        str(session_request.reviewer) if session_request.reviewer else "",
        timezone.localtime(session_request.reviewed_at).strftime("%Y-%m-%d %H:%M")
        if session_request.reviewed_at
        else "",
        session_request.decision_reason,
    ]


@login_required
def export_requests_xlsx(request):
    _require_reviewer(request.user)

    visible = visible_requests_for(request.user).select_related(
        "requesting_unit", "place", "submitted_by", "reviewer"
    )
    visible = filter_requests(visible, request.GET, is_reviewer=True)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Session requests"
    sheet.append(_xlsx_headers())
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"

    for session_request in visible:
        sheet.append(_xlsx_row(session_request))

    widths = [38, 32, 28, 24, 18, 18, 14, 28, 24, 18, 40]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width

    buffer = BytesIO()
    workbook.save(buffer)

    response = HttpResponse(
        buffer.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
    response["Content-Disposition"] = 'attachment; filename="session-requests.xlsx"'
    return response


@login_required
def reports_audit(request):
    _require_reviewer(request.user)

    history = RequestStatusHistory.objects.select_related(
        "changed_by", "request", "request__requesting_unit", "request__place"
    ).order_by("-created_at")
    history = filter_history(history, request.GET)

    paginator = Paginator(history, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    querystring = request.GET.copy()
    querystring.pop("page", None)

    context = {
        "page_obj": page_obj,
        "history": page_obj.object_list,
        "statuses": SessionRequest.Status.choices,
        "actors": User.objects.filter(request_status_changes__isnull=False)
        .distinct()
        .order_by("full_name", "email"),
        "querystring": querystring.urlencode(),
    }
    context.update(history_filter_context(request.GET))
    return render(request, "bookings/audit.html", context)


def _default_report_range():
    today = timezone.localdate()
    date_from = today.replace(day=1)
    next_month = (date_from + timedelta(days=32)).replace(day=1)
    return date_from, next_month - timedelta(days=1)


@login_required
def reports_utilization(request):
    _require_reviewer(request.user)

    default_from, default_to = _default_report_range()
    date_from = parse_date(request.GET.get("date_from", "")) or default_from
    date_to = parse_date(request.GET.get("date_to", "")) or default_to
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    heatmap = build_utilization_heatmap(
        places=Place.objects.filter(is_active=True),
        date_from=date_from,
        date_to=date_to,
    )
    return render(
        request,
        "bookings/utilization.html",
        {
            **heatmap,
            "date_from_value": date_from.isoformat(),
            "date_to_value": date_to.isoformat(),
        },
    )
