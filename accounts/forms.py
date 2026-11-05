from django.contrib.auth.forms import AuthenticationForm


class EmailAuthenticationForm(AuthenticationForm):
    """Authentication form tuned for the email-first login screen."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Email"
        self.fields["username"].widget.attrs.update(
            {
                "autofocus": True,
                "autocomplete": "email",
                "inputmode": "email",
                "placeholder": "name@example.com",
            }
        )
