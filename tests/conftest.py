from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.utils import timezone

from accounts.models import User
from places.models import Place
from units.models import Unit


@pytest.fixture(autouse=True)
def use_locmem_email(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


@pytest.fixture
def cs_cis(db):
    return Unit.objects.create(
        name="CS/CIS Beni-Suef Chapter",
        slug="cs-cis",
        kind=Unit.Kind.CHAPTER,
    )


@pytest.fixture
def board(db, cs_cis):
    return Unit.objects.create(
        name="Agentic AI",
        slug="agentic-ai",
        kind=Unit.Kind.BOARD,
        parent=cs_cis,
    )


@pytest.fixture
def other_board(db, cs_cis):
    return Unit.objects.create(
        name="Machine Learning",
        slug="machine-learning",
        kind=Unit.Kind.BOARD,
        parent=cs_cis,
    )


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(
        email="admin@example.com",
        password="test-password",
        full_name="Admin User",
    )


@pytest.fixture
def officer(db, cs_cis):
    return User.objects.create_user(
        email="officer@example.com",
        password="test-password",
        full_name="Chapter Officer",
        unit=cs_cis,
        account_type=User.AccountType.OFFICER,
    )


@pytest.fixture
def board_user(db, board):
    return User.objects.create_user(
        email="board@example.com",
        password="test-password",
        full_name="Board Account",
        unit=board,
        account_type=User.AccountType.BOARD_ACCOUNT,
    )


@pytest.fixture
def other_board_user(db, other_board):
    return User.objects.create_user(
        email="other-board@example.com",
        password="test-password",
        full_name="Other Board Account",
        unit=other_board,
        account_type=User.AccountType.BOARD_ACCOUNT,
    )


@pytest.fixture
def place(db):
    return Place.objects.create(
        name="Room A",
        building="Chapter HQ",
        room="101",
        opening_time=time(9, 0),
        closing_time=time(18, 0),
    )


@pytest.fixture
def tomorrow():
    return timezone.localdate() + timedelta(days=1)


@pytest.fixture
def start_at(tomorrow):
    return datetime.combine(
        tomorrow,
        time(10, 0),
        tzinfo=ZoneInfo(settings.TIME_ZONE),
    )


@pytest.fixture
def end_at(start_at):
    return start_at + timedelta(minutes=60)
