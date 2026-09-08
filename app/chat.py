"""Validation of room chat text, mirroring the client contract exactly.

Chat text is *not* NFKC-normalized on purpose: unlike nicknames, a chat line
is displayed once and never compared, so the server keeps what the user
typed and only strips the surrounding whitespace.
"""

from __future__ import annotations

import unicodedata

from .errors import ChatTextInvalidError

MIN_CHAT_TEXT_LENGTH = 1
MAX_CHAT_TEXT_LENGTH = 200
FORBIDDEN_UNICODE_CATEGORIES = frozenset({"Cc", "Cs", "Zl", "Zp"})

#: Rolling window and the number of messages one connection may send in it.
CHAT_RATE_LIMIT_WINDOW_SECONDS = 1.0
CHAT_RATE_LIMIT_MAX_MESSAGES = 2


def normalize_chat_text(value: object) -> str:
    if not isinstance(value, str):
        raise ChatTextInvalidError("Chat text must be a string.")
    normalized = value.strip()
    if not MIN_CHAT_TEXT_LENGTH <= len(normalized) <= MAX_CHAT_TEXT_LENGTH:
        raise ChatTextInvalidError(
            f"Chat text must contain {MIN_CHAT_TEXT_LENGTH} to "
            f"{MAX_CHAT_TEXT_LENGTH} characters."
        )
    if any(
        unicodedata.category(character) in FORBIDDEN_UNICODE_CATEGORIES
        for character in normalized
    ):
        raise ChatTextInvalidError(
            "Chat text must not contain control or line separator characters."
        )
    return normalized
