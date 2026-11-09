from django.contrib import admin
from django.urls import include, path

from accounts.views import ChapterLoginView, ChapterLogoutView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", ChapterLoginView.as_view(), name="login"),
    path("logout/", ChapterLogoutView.as_view(), name="logout"),
    path("", include("notifications.urls")),
    path("", include("bookings.urls")),
]
