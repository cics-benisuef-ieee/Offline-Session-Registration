import pytest
from django.core.management import call_command

from accounts.management.commands.seed_board_accounts import board_email
from accounts.models import User
from units.models import Unit

pytestmark = pytest.mark.django_db


def test_seed_board_accounts_creates_one_per_board():
    call_command("seed_units")
    call_command("seed_board_accounts", password="test-password")

    boards = Unit.objects.filter(kind=Unit.Kind.BOARD, is_active=True)
    assert boards.count() == 17
    assert User.objects.filter(
        account_type=User.AccountType.BOARD_ACCOUNT
    ).count() == 17

    for board in boards:
        user = User.objects.get(email=board_email(board.name))
        assert user.unit == board
        assert user.account_type == User.AccountType.BOARD_ACCOUNT
        assert user.is_active is True
        assert user.check_password("test-password") is True

    assert board_email("UI/UX") == "ui-ux@cics.benisuef.ieee"
    assert board_email("Human Resources") == "hr@benisuef.ieee"
    assert (
        board_email("Digital Marketing")
        == "digital-marketing@benisuef.ieee"
    )
    assert board_email("Event Management") == "em@wie.benisuef.ieee"


def test_seed_board_accounts_is_idempotent():
    call_command("seed_units")
    call_command("seed_board_accounts", password="first-password")
    call_command("seed_board_accounts", password="second-password")

    assert User.objects.filter(
        account_type=User.AccountType.BOARD_ACCOUNT
    ).count() == 17
    assert User.objects.get(
        email="agentic-ai@cics.benisuef.ieee"
    ).check_password("second-password") is True
    assert User.objects.get(
        email="hr@benisuef.ieee"
    ).check_password("second-password") is True
    assert User.objects.get(
        email="em@wie.benisuef.ieee"
    ).check_password("second-password") is True
