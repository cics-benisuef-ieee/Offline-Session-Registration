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


class Command(BaseCommand):
    help = "Create or update the CS/CIS Beni-Suef Chapter and its boards."

    @transaction.atomic
    def handle(self, *args, **options):
        chapter, _ = Unit.objects.update_or_create(
            slug="cs-cis",
            defaults={
                "name": "CS/CIS Beni-Suef Chapter",
                "kind": Unit.Kind.CHAPTER,
                "parent": None,
                "is_active": True,
            },
        )
        # Deactivate units left behind by the previous branch-level seed.
        Unit.objects.filter(slug__in=["branch", "wie"]).update(is_active=False)

        for board_name in BOARDS:
            slug = board_name.lower().replace(" ", "-").replace("/", "-")
            Unit.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": board_name,
                    "kind": Unit.Kind.BOARD,
                    "parent": chapter,
                    "is_active": True,
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded CS/CIS Beni-Suef Chapter and {len(BOARDS)} boards."
            )
        )
