"""Account domain values, validation, and password hashing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from .errors import (
    AccountIdEmptyError,
    AccountIdInvalidCharacterError,
    AccountIdNotStringError,
    AccountIdTooLongError,
    AccountIdTooShortError,
    PasswordNotStringError,
    PasswordTooLongError,
    PasswordTooShortError,
    PasswordWhitespaceError,
)
from .nickname import normalize_nickname

MIN_ACCOUNT_ID_LENGTH = 4
MAX_ACCOUNT_ID_LENGTH = 20
MIN_PASSWORD_LENGTH = 4
MAX_PASSWORD_LENGTH = 20
_ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z]{4,20}$")
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16


@dataclass(frozen=True)
class Account:
    account_id: str
    nickname: str
    created_at: str


def normalize_account_id(value: object) -> str:
    if not isinstance(value, str):
        raise AccountIdNotStringError()
    normalized = value.strip()
    if not normalized:
        raise AccountIdEmptyError()
    if len(normalized) < MIN_ACCOUNT_ID_LENGTH:
        raise AccountIdTooShortError()
    if len(normalized) > MAX_ACCOUNT_ID_LENGTH:
        raise AccountIdTooLongError()
    if not _ACCOUNT_ID_PATTERN.fullmatch(normalized):
        raise AccountIdInvalidCharacterError()
    return normalized.lower()


def validate_password(value: object) -> str:
    if not isinstance(value, str):
        raise PasswordNotStringError()
    if len(value) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShortError()
    if len(value) > MAX_PASSWORD_LENGTH:
        raise PasswordTooLongError()
    if any(character.isspace() for character in value):
        raise PasswordWhitespaceError()
    return value


def validated_nickname(value: object) -> str:
    return normalize_nickname(value)


def hash_password(password: object) -> str:
    validated = validate_password(password)
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        validated.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return "$".join(
        (
            "scrypt", str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: object, encoded_hash: str) -> bool:
    try:
        validated = validate_password(password)
        algorithm, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded_hash.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.b64decode(raw_salt, validate=True)
        expected = base64.b64decode(raw_digest, validate=True)
        actual = hashlib.scrypt(
            validated.encode("utf-8"), salt=salt,
            n=int(raw_n), r=int(raw_r), p=int(raw_p), dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)
