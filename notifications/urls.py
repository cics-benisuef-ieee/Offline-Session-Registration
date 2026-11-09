from django.urls import path

from . import views

urlpatterns = [
    path("notifications/", views.notification_list, name="notification_list"),
    path(
        "notifications/count/",
        views.notification_count,
        name="notification_count",
    ),
    path(
        "notifications/read-all/",
        views.notification_read_all,
        name="notification_read_all",
    ),
    path(
        "notifications/<int:pk>/read/",
        views.notification_read,
        name="notification_read",
    ),
    path(
        "settings/notifications/",
        views.notification_preferences,
        name="notification_preferences",
    ),
    path("cron/reminders/", views.cron_reminders, name="cron_reminders"),
    path("cron/digest/", views.cron_digest, name="cron_digest"),
]
