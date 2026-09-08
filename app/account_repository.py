"""Persistent account creation and credential verification."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .account import Account, hash_password, normalize_account_id, validated_nickname, verify_password
from .database import Database
from .errors import AccountIdTakenError


class AccountRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def initialize(self) -> None:
        self.database.initialize()

    def create_account(self, account_id: object, password: object, nickname: object) -> Account:
        normalized_id = normalize_account_id(account_id)
        normalized_nickname = validated_nickname(nickname)
        password_hash = hash_password(password)
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            with self.database.connect() as connection:
                connection.execute(
                    "INSERT INTO accounts(account_id, password_hash, nickname, created_at) VALUES (?, ?, ?, ?)",
                    (normalized_id, password_hash, normalized_nickname, created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise AccountIdTakenError() from exc
        return Account(normalized_id, normalized_nickname, created_at)

    def authenticate(self, account_id: object, password: object) -> Account | None:
        normalized_id = normalize_account_id(account_id)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT account_id, password_hash, nickname, created_at FROM accounts WHERE account_id = ?",
                (normalized_id,),
            ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            return None
        return Account(row["account_id"], row["nickname"], row["created_at"])

    def list_accounts(self) -> list[Account]:
        """Return every account without exposing password hashes."""
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT account_id, nickname, created_at
                FROM accounts
                ORDER BY account_id COLLATE NOCASE
                """
            ).fetchall()
        return [
            Account(row["account_id"], row["nickname"], row["created_at"])
            for row in rows
        ]

    def get_account_count(self) -> int:
        """Return the number of accounts stored in the database."""
        with self.database.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM accounts").fetchone()
        return int(row["count"])

    def delete_account(self, account_id: object) -> bool:
        """Delete one account and report whether it existed."""
        normalized_id = normalize_account_id(account_id)
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM accounts WHERE account_id = ?", (normalized_id,)
            )
        return cursor.rowcount > 0
