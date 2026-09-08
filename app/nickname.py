"""Normalization and validation for client display nicknames."""

from __future__ import annotations

import unicodedata

from .errors import (
    NicknameEmptyError,
    NicknameInvalidCharacterError,
    NicknameNotStringError,
    NicknameTooLongError,
)

MIN_NICKNAME_LENGTH = 1
MAX_NICKNAME_LENGTH = 20
FORBIDDEN_UNICODE_CATEGORIES = frozenset({"Cc", "Cs", "Zl", "Zp"})


def normalize_nickname(value: object) -> str:
    if not isinstance(value, str):
        raise NicknameNotStringError()
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        raise NicknameEmptyError()
    if len(normalized) > MAX_NICKNAME_LENGTH:
        raise NicknameTooLongError()
    if any(
        unicodedata.category(character) in FORBIDDEN_UNICODE_CATEGORIES
        for character in normalized
    ):
        raise NicknameInvalidCharacterError()
    return normalized
