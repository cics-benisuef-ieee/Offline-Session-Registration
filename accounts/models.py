from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("The email address is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    class AccountType(models.TextChoices):
        OFFICER = "OFFICER", "Officer"
        BOARD_ACCOUNT = "BOARD_ACCOUNT", "Board account"

    username = None
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    unit = models.ForeignKey(
        "units.Unit",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="users",
    )
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
        default=AccountType.OFFICER,
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["unit"],
                condition=models.Q(account_type="BOARD_ACCOUNT"),
                name="one_board_account_per_board",
            )
        ]

    def __str__(self):
        return self.full_name or self.email

    def clean(self):
        super().clean()
        if self.account_type == self.AccountType.BOARD_ACCOUNT:
            if not self.unit_id:
                raise ValidationError(
                    {"unit": "Board accounts must be linked to a board."}
                )
            if self.unit.kind != "board":
                raise ValidationError(
                    {"unit": "Board accounts must be linked to a board."}
                )
        elif self.unit_id and self.unit.kind == "board":
            raise ValidationError(
                {"unit": "Officer accounts must belong to a branch or chapter."}
            )

    @property
    def is_officer(self):
        return self.is_superuser or self.account_type == self.AccountType.OFFICER

    @property
    def is_board_account(self):
        return self.account_type == self.AccountType.BOARD_ACCOUNT
