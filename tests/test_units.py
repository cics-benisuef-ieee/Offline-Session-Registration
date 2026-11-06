import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command

from units.models import Unit

pytestmark = pytest.mark.django_db


def test_seed_units_creates_chapter_branch_and_boards():
    Unit.objects.create(name="Legacy Branch", slug="branch", kind=Unit.Kind.BRANCH)
    Unit.objects.create(name="Legacy Affinity Group", slug="wie", kind=Unit.Kind.CHAPTER)

    call_command("seed_units")

    chapter = Unit.objects.get(slug="cs-cis")
    assert chapter.name == "CS/CIS Beni-Suef Chapter"
    assert chapter.kind == Unit.Kind.CHAPTER
    assert chapter.parent is None
    assert chapter.is_active is True

    branch = Unit.objects.get(slug="branch")
    assert branch.name == "Beni-Suef Student Branch"
    assert branch.kind == Unit.Kind.BRANCH
    assert branch.parent is None
    assert branch.is_active is True

    assert Unit.objects.filter(kind=Unit.Kind.BOARD, parent=chapter).count() == 11
    assert Unit.objects.filter(kind=Unit.Kind.BOARD, parent=branch).count() == 5
    assert Unit.objects.get(slug="wie").is_active is True


def test_board_can_belong_to_a_branch():
    branch = Unit.objects.create(
        name="Beni-Suef Student Branch",
        slug="branch",
        kind=Unit.Kind.BRANCH,
    )
    board = Unit(
        name="Human Resources",
        slug="human-resources",
        kind=Unit.Kind.BOARD,
        parent=branch,
    )

    board.full_clean()


def test_board_cannot_belong_to_another_board():
    branch = Unit.objects.create(
        name="Beni-Suef Student Branch",
        slug="branch",
        kind=Unit.Kind.BRANCH,
    )
    parent_board = Unit.objects.create(
        name="Parent Board",
        slug="parent-board",
        kind=Unit.Kind.BOARD,
        parent=branch,
    )
    child_board = Unit(
        name="Child Board",
        slug="child-board",
        kind=Unit.Kind.BOARD,
        parent=parent_board,
    )

    with pytest.raises(ValidationError):
        child_board.full_clean()
