# Create Account Server Contract

## Scope

This contract creates an account only. A successful response does not log the
connection in and does not replace the existing `set_nickname` flow. Account
login will be introduced as a separate protocol operation.

The server initializes the SQLite account database during application startup.
The default path is `server/data/accounts.db`; operators may override it with
the `OMOK_ACCOUNT_DB_PATH` environment variable.

## Request

The request is accepted only while the connection is outside a room and is not
already authenticated.

```json
{
  "type": "create_account",
  "account_id": "WataUser",
  "password": "pass1234",
  "nickname": "WATA"
}
```

### Account ID

- Required JSON string.
- Leading and trailing whitespace is removed.
- 4 to 20 ASCII letters (`A-Z`, `a-z`) only.
- Stored in lowercase and compared case-insensitively.
- Must be unique.

### Password

- Required JSON string.
- 4 to 20 Unicode characters.
- Whitespace is forbidden anywhere in the password. This includes spaces,
  tabs, and line-separator characters.
- The value is not trimmed, case-folded, or Unicode-normalized.
- The plaintext value is never stored. The database stores a salted scrypt
  hash.

### Nickname

- Required JSON string.
- NFKC-normalized and trimmed.
- 1 to 20 Unicode characters after normalization.
- Control, surrogate, and line-separator characters are forbidden.
- Duplicate nicknames are allowed.

## Success response

```json
{
  "type": "account_created",
  "account_id": "watauser",
  "nickname": "WATA"
}
```

The returned `account_id` is the normalized value stored by the server.

## Error response

Errors use the existing envelope:

```json
{
  "type": "error",
  "code": "ACCOUNT_ID_TAKEN",
  "message": "The account ID is already in use."
}
```

Possible `code` values are:

- Account ID: `ACCOUNT_ID_MISSING`, `ACCOUNT_ID_NOT_STRING`,
  `ACCOUNT_ID_EMPTY`, `ACCOUNT_ID_TOO_SHORT`, `ACCOUNT_ID_TOO_LONG`,
  `ACCOUNT_ID_INVALID_CHARACTER`, `ACCOUNT_ID_TAKEN`.
- Password: `PASSWORD_MISSING`, `PASSWORD_NOT_STRING`, `PASSWORD_TOO_SHORT`,
  `PASSWORD_TOO_LONG`, `PASSWORD_WHITESPACE_NOT_ALLOWED`.
- Nickname: `NICKNAME_MISSING`, `NICKNAME_NOT_STRING`, `NICKNAME_EMPTY`,
  `NICKNAME_TOO_LONG`, `NICKNAME_INVALID_CHARACTER`.
- Connection state: `ALREADY_AUTHENTICATED`,
  `ACCOUNT_OPERATION_NOT_AVAILABLE`, `ACCOUNT_REQUEST_PENDING`.
- Unexpected persistence or hashing failure: `ACCOUNT_CREATE_FAILED`.

`ACCOUNT_CREATE_FAILED` does not expose database or hashing details to the
client. Those details are written only to the server log, without the raw
password or request payload.
