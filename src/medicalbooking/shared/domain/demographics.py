"""Patient sex and birth date rules shared by registration and the clinical profile."""

from __future__ import annotations

from datetime import date

from medicalbooking.shared.domain.errors import ValidationFailed

SEXES = ("M", "F")
MAX_AGE_YEARS = 120


def validate_sex(value: str) -> str:
    if value not in SEXES:
        raise ValidationFailed("Sex must be M or F")
    return value


def parse_birth_date(value: str, today: date) -> date:
    """A YYYY-MM-DD date that is not in the future and gives an age of at most 120 years."""
    try:
        if len(value) != 10:
            raise ValueError(value)
        born = date.fromisoformat(value)
    except ValueError:
        raise ValidationFailed("Birth date must be a valid date as YYYY-MM-DD") from None
    if born > today:
        raise ValidationFailed("Birth date cannot be in the future")
    birthday_passed = (today.month, today.day) >= (born.month, born.day)
    if today.year - born.year - (0 if birthday_passed else 1) > MAX_AGE_YEARS:
        raise ValidationFailed(f"Age cannot be over {MAX_AGE_YEARS} years")
    return born
