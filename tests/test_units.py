import pytest
from django.core.management import call_command

from units.models import Unit

pytestmark = pytest.mark.django_db


def test_seed_units_creates_chapter_and_boards():
    Unit.objects.create(name="Legacy Branch", slug="branch", kind=Unit.Kind.BRANCH)
    Unit.objects.create(name="Legacy Affinity Group", slug="wie", kind=Unit.Kind.CHAPTER)

    call_command("seed_units")

    chapter = Unit.objects.get(slug="cs-cis")
    assert chapter.name == "CS/CIS Beni-Suef Chapter"
    assert chapter.kind == Unit.Kind.CHAPTER
    assert chapter.parent is None
    assert chapter.is_active is True
    assert Unit.objects.filter(kind=Unit.Kind.BOARD, parent=chapter).count() == 11
    assert Unit.objects.get(slug="branch").is_active is False
    assert Unit.objects.get(slug="wie").is_active is False
