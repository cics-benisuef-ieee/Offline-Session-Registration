from django.core.exceptions import ValidationError
from django.db import models


class Unit(models.Model):
    class Kind(models.TextChoices):
        BRANCH = "branch", "Branch"
        CHAPTER = "chapter", "Chapter"
        BOARD = "board", "Board"

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.BOARD)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.kind == self.Kind.BOARD:
            if not self.parent_id:
                raise ValidationError({"parent": "A board must belong to a chapter."})
            if self.parent.kind != self.Kind.CHAPTER:
                raise ValidationError({"parent": "A board's parent must be a chapter."})
        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError({"parent": "A unit cannot be its own parent."})

    @property
    def is_board(self):
        return self.kind == self.Kind.BOARD

    @property
    def is_entity(self):
        return self.kind in {self.Kind.BRANCH, self.Kind.CHAPTER}
