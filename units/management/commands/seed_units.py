from django.core.management.base import BaseCommand
from django.db import transaction

from units.models import Unit

BOARDS = [
    "Agentic AI",
    "Machine Learning",
    "Data Analysis",
    "Red-Team",
    "Network",
    "Mechanical Design",
    "Embedded Systems",
    "Front-end",
    "Back-end",
    "Flutter",
    "UI/UX",
]

BRANCH_BOARDS = [
    "Human Resources",
    "Entrepreneurship",
    "Visuals",
    "Presentation",
    "Digital Marketing",
]

WIE_BOARDS = [
    "Event Management",
]


def _slug(name):
    return name.lower().replace(" ", "-").replace("/", "-")


class Command(BaseCommand):
    help = (
        "Create or update the CS/CIS chapter, its boards, the Beni-Suef Student "
        "Branch, the branch committees, and the WIE boards."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        branch, _ = Unit.objects.update_or_create(
            slug="branch",
            defaults={
                "name": "Beni-Suef Student Branch",
                "kind": Unit.Kind.BRANCH,
                "parent": None,
                "is_active": True,
            },
        )
        chapter, _ = Unit.objects.update_or_create(
            slug="cs-cis",
            defaults={
                "name": "CS/CIS Beni-Suef Chapter",
                "kind": Unit.Kind.CHAPTER,
                "parent": None,
                "is_active": True,
            },
        )
        wie, _ = Unit.objects.update_or_create(
            slug="wie",
            defaults={
                "name": "WIE Beni-Suef Affinity Group",
                "kind": Unit.Kind.CHAPTER,
                "parent": branch,
                "is_active": True,
            },
        )

        for board_name in BOARDS:
            Unit.objects.update_or_create(
                slug=_slug(board_name),
                defaults={
                    "name": board_name,
                    "kind": Unit.Kind.BOARD,
                    "parent": chapter,
                    "is_active": True,
                },
            )

        for board_name in BRANCH_BOARDS:
            Unit.objects.update_or_create(
                slug=_slug(board_name),
                defaults={
                    "name": board_name,
                    "kind": Unit.Kind.BOARD,
                    "parent": branch,
                    "is_active": True,
                },
            )

        for board_name in WIE_BOARDS:
            Unit.objects.update_or_create(
                slug=_slug(board_name),
                defaults={
                    "name": board_name,
                    "kind": Unit.Kind.BOARD,
                    "parent": wie,
                    "is_active": True,
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Seeded CS/CIS Beni-Suef Chapter, Beni-Suef Student Branch, "
                "WIE Beni-Suef Affinity Group, "
                f"{len(BOARDS)} chapter boards, "
                f"{len(BRANCH_BOARDS)} branch boards, and "
                f"{len(WIE_BOARDS)} WIE board."
            )
        )
