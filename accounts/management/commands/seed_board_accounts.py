import os
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from units.models import Unit

EMAIL_DOMAIN = "cics.benisuef.ieee"

EMAIL_OVERRIDES = {
    "Human Resources": "hr@benisuef.ieee",
    "Entrepreneurship": "entrepreneurship@benisuef.ieee",
    "Visuals": "visuals@benisuef.ieee",
    "Presentation": "presentation@benisuef.ieee",
    "Digital Marketing": "digital-marketing@benisuef.ieee",
}


def board_email(board_name):
    """Build the board account email from the board name."""
    if board_name in EMAIL_OVERRIDES:
        return EMAIL_OVERRIDES[board_name]
    local_part = re.sub(r"[^a-z0-9]+", "-", board_name.lower()).strip("-")
    return f"{local_part}@{EMAIL_DOMAIN}"


class Command(BaseCommand):
    help = "Create or update one board account for every active board."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=os.environ.get("BOARD_ACCOUNT_PASSWORD"),
            help=(
                "Password for every board account. Defaults to the "
                "BOARD_ACCOUNT_PASSWORD environment variable."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        password = options["password"]
        if not password:
            raise CommandError(
                "Provide --password or set BOARD_ACCOUNT_PASSWORD."
            )

        boards = Unit.objects.filter(
            kind=Unit.Kind.BOARD,
            is_active=True,
        ).order_by("name")
        if not boards.exists():
            raise CommandError(
                "No active boards found. Run seed_units first."
            )

        created_count = 0
        updated_count = 0
        for board in boards:
            email = board_email(board.name)
            user = User.objects.filter(
                unit=board,
                account_type=User.AccountType.BOARD_ACCOUNT,
            ).first()
            if user is None:
                email_owner = User.objects.filter(email=email).first()
                if email_owner is not None and email_owner.unit_id != board.pk:
                    raise CommandError(
                        f"{email} already belongs to another account."
                    )
                user = email_owner
            if user is None:
                user = User(email=email)
                created_count += 1
            else:
                updated_count += 1

            if User.objects.filter(email=email).exclude(pk=user.pk).exists():
                raise CommandError(
                    f"{email} already belongs to another account."
                )

            user.email = email
            user.full_name = f"{board.name} Board Account"
            user.unit = board
            user.account_type = User.AccountType.BOARD_ACCOUNT
            user.is_active = True
            user.is_staff = False
            user.is_superuser = False
            user.set_password(password)
            user.save()

            self.stdout.write(f"  {board.name}: {email}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {created_count + updated_count} board accounts "
                f"({created_count} created, {updated_count} updated)."
            )
        )
