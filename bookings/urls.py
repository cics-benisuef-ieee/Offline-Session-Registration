from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("requests/", views.request_list, name="request_list"),
    path("requests/new/", views.request_create, name="request_create"),
    path("requests/series/new/", views.series_create, name="series_create"),
    path(
        "requests/series/preview/",
        views.series_preview,
        name="series_preview",
    ),
    path("series/<int:pk>/", views.series_detail, name="series_detail"),
    path("series/<int:pk>/approve/", views.series_approve, name="series_approve"),
    path("series/<int:pk>/reject/", views.series_reject, name="series_reject"),
    path("series/<int:pk>/cancel/", views.series_cancel, name="series_cancel"),
    path(
        "series/<int:pk>/reschedule/",
        views.series_reschedule,
        name="series_reschedule",
    ),
    path("requests/<uuid:pk>/", views.request_detail, name="request_detail"),
    path("requests/<uuid:pk>/cancel/", views.request_cancel, name="request_cancel"),
    path("requests/<uuid:pk>/delete/", views.request_delete, name="request_delete"),
    path(
        "requests/<uuid:pk>/reschedule/",
        views.request_reschedule,
        name="request_reschedule",
    ),
    path("requests/<uuid:pk>/ics/", views.request_ics, name="request_ics"),
    path("review/", views.review_queue, name="review_queue"),
    path("review/bulk/", views.request_bulk_review, name="request_bulk_review"),
    path("review/<uuid:pk>/", views.request_review, name="request_review"),
    path("availability/next/", views.availability_next, name="availability_next"),
    path("availability/", views.availability, name="availability"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("calendar/events/", views.calendar_events, name="calendar_events"),
    path("calendar/feed/rotate/", views.calendar_feed_rotate, name="calendar_feed_rotate"),
    path("calendar/feed/<uuid:token>.ics", views.calendar_feed, name="calendar_feed"),
    path("reports/", views.reports, name="reports"),
    path("reports/requests.csv", views.export_requests_csv, name="export_requests_csv"),
    path(
        "reports/requests.xlsx",
        views.export_requests_xlsx,
        name="export_requests_xlsx",
    ),
    path("reports/audit/", views.reports_audit, name="reports_audit"),
    path(
        "reports/utilization/",
        views.reports_utilization,
        name="reports_utilization",
    ),
]
