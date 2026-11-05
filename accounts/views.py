from django.contrib import messages
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse_lazy

from .forms import EmailAuthenticationForm


class ChapterLoginView(LoginView):
    """Always send a successful sign-in to the dashboard."""

    template_name = "registration/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True
    success_url = reverse_lazy("dashboard")

    def get_success_url(self):
        # Deliberately ignore ?next= so sign-in always lands on the dashboard.
        return str(self.success_url)


class ChapterLogoutView(LogoutView):
    next_page = reverse_lazy("login")

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        messages.success(request, "You have been signed out.")
        return response
