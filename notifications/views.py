import hmac
import json
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
)

from .digest import send_officer_digest
from .forms import NotificationPreferenceForm
from .models import NotificationPreference, PushSubscription
from .reminders import send_due_reminders

MAX_PUSH_SUBSCRIPTIONS = 10


def _cron_authorized(request):
    secret = settings.CRON_SECRET
    if not secret:
        return False
    header = request.headers.get("Authorization", "")
    return hmac.compare_digest(header, f"Bearer {secret}")


@csrf_exempt
@require_POST
def cron_reminders(request):
    if not _cron_authorized(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    return JsonResponse(send_due_reminders())


@csrf_exempt
@require_http_methods(["GET", "POST"])
def cron_digest(request):
    if not _cron_authorized(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    return JsonResponse(send_officer_digest())


@login_required
@ensure_csrf_cookie
def notification_list(request):
    notifications = request.user.notifications.select_related("request")[:50]
    unread_count = request.user.notifications.filter(read_at__isnull=True).count()
    return render(
        request,
        "notifications/list.html",
        {
            "notifications": notifications,
            "unread_count": unread_count,
        },
    )


@login_required
def notification_count(request):
    unread_count = request.user.notifications.filter(read_at__isnull=True).count()
    return render(
        request,
        "notifications/partials/bell.html",
        {"unread_count": unread_count},
    )


@login_required
@require_POST
def notification_read(request, pk):
    notification = get_object_or_404(request.user.notifications, pk=pk)
    notification.mark_read()

    if request.headers.get("HX-Request"):
        return render(
            request,
            "notifications/partials/notification_row.html",
            {"notification": notification},
        )

    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect("notification_list")


@login_required
@require_POST
def notification_read_all(request):
    request.user.notifications.filter(read_at__isnull=True).update(
        read_at=timezone.now()
    )
    return redirect("notification_list")


@login_required
@ensure_csrf_cookie
def notification_preferences(request):
    preference = NotificationPreference.for_user(request.user)
    form = NotificationPreferenceForm(request.POST or None, instance=preference)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Notification preferences saved.")
        return redirect("notification_preferences")

    return render(
        request,
        "notifications/preferences.html",
        {"form": form, "preference": preference},
    )


@require_GET
def service_worker(request):
    path = Path(settings.BASE_DIR) / "static" / "js" / "service-worker.js"
    if not path.exists():
        raise Http404("Service worker not found.")
    response = HttpResponse(
        path.read_text(encoding="utf-8"),
        content_type="application/javascript",
    )
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache, must-revalidate"
    return response


def _parse_json(request):
    try:
        return json.loads(request.body or "{}")
    except (TypeError, ValueError):
        return None


def _endpoint_allowed(endpoint):
    parsed = urlparse(endpoint)
    if parsed.scheme == "https" and parsed.netloc:
        return True
    return (
        settings.DEBUG
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1"}
    )


@login_required
@require_POST
def push_subscribe(request):
    payload = _parse_json(request)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    endpoint = (payload.get("endpoint") or "").strip()
    keys = payload.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        return JsonResponse({"error": "Missing subscription fields."}, status=400)
    if len(endpoint) > 500:
        return JsonResponse({"error": "Subscription endpoint is too long."}, status=400)
    if not _endpoint_allowed(endpoint):
        return JsonResponse({"error": "Invalid subscription endpoint."}, status=400)

    existing = PushSubscription.objects.filter(endpoint=endpoint).first()
    if (
        existing is None
        and request.user.push_subscriptions.count() >= MAX_PUSH_SUBSCRIPTIONS
    ):
        return JsonResponse(
            {"error": "Too many browser subscriptions."},
            status=400,
        )

    subscription, _ = PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": request.user,
            "p256dh": p256dh[:200],
            "auth": auth[:100],
            "user_agent": request.headers.get("User-Agent", "")[:255],
        },
    )
    return JsonResponse({"ok": True, "id": subscription.pk})


@login_required
@require_POST
def push_unsubscribe(request):
    payload = _parse_json(request)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    endpoint = (payload.get("endpoint") or "").strip()
    if endpoint:
        request.user.push_subscriptions.filter(endpoint=endpoint).delete()
    return JsonResponse({"ok": True})
