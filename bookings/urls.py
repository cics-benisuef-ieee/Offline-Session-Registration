from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("requests/", views.request_list, name="request_list"),
    path("requests/new/", views.request_create, name="request_create"),
    path("requests/<uuid:pk>/", views.request_detail, name="request_detail"),
    path("requests/<uuid:pk>/cancel/", views.request_cancel, name="request_cancel"),
    path("review/", views.review_queue, name="review_queue"),
    path("review/<uuid:pk>/", views.request_review, name="request_review"),
    path("availability/", views.availability, name="availability"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("calendar/events/", views.calendar_events, name="calendar_events"),
    path("reports/", views.reports, name="reports"),
    path("reports/requests.csv", views.export_requests_csv, name="export_requests_csv"),
]
