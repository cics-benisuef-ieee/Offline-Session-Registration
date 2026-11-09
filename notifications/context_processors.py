from django.conf import settings


def push(request):
    return {
        "WEB_PUSH_ENABLED": settings.WEB_PUSH_ENABLED,
        "VAPID_PUBLIC_KEY": settings.VAPID_PUBLIC_KEY,
    }
