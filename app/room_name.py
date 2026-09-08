"""Server-side room name normalization and validation."""

from __future__ import annotations

import unicodedata

from .errors import InvalidRoomNameError

MIN_ROOM_NAME_LENGTH = 1
MAX_ROOM_NAME_LENGTH = 30
FORBIDDEN_UNICODE_CATEGORIES = frozenset({"Cc", "Cs", "Zl", "Zp"})


def normalize_room_name(value: object) -> str:
    """Return the canonical display name or raise INVALID_ROOM_NAME.

    Length is measured in Unicode code points after NFKC normalization and
    trimming. Internal whitespace is preserved.
    """
    if not isinstance(value, str):
        raise InvalidRoomNameError("room_name must be a string.")

    normalized = unicodedata.normalize("NFKC", value).strip()
    if not MIN_ROOM_NAME_LENGTH <= len(normalized) <= MAX_ROOM_NAME_LENGTH:
        raise InvalidRoomNameError(
            f"room_name must contain {MIN_ROOM_NAME_LENGTH} to "
            f"{MAX_ROOM_NAME_LENGTH} Unicode characters."
        )

    if any(
        unicodedata.category(character) in FORBIDDEN_UNICODE_CATEGORIES
        for character in normalized
    ):
        raise InvalidRoomNameError(
            "room_name must not contain control or line-separator characters."
        )
    return normalized


def room_name_key(normalized_room_name: str) -> str:
    """Return the locale-independent key used for duplicate detection."""
    return normalized_room_name.casefold()
