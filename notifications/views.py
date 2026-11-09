import hmac

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from .digest import send_officer_digest
from .forms import NotificationPreferenceForm
from .models import NotificationPreference
from .reminders import send_due_reminders


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
