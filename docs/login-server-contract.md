# Login Server Contract

## Scope

Login authenticates one WebSocket connection against the account database.
The server does not maintain a global account session registry: the same
account may log in concurrently from multiple devices, and room/game identity
continues to use each connection's unique `connection_id`.

Login is accepted only while the connection is in the lobby and is not already
authenticated.

## Request

```json
{
  "type": "login",
  "account_id": "WataUser",
  "password": "pass1234"
}
```

- `account_id` and `password` are required JSON strings.
- Account IDs are normalized to lowercase according to the create-account
  rules.
- Passwords are checked without trimming, case folding, or Unicode
  normalization.
- Invalid account-ID or password values and incorrect credentials produce the
  same `INVALID_CREDENTIALS` response.

## Success response

```json
{
  "type": "login_succeeded",
  "account_id": "watauser",
  "nickname": "WATA"
}
```

On success, the server stores the normalized account ID and database nickname
in that connection's `ClientSession`. A nickname previously set by the guest
flow is replaced. Logging in does not affect another connection using the same
account.

An authenticated connection cannot use `set_nickname`; the account nickname
remains authoritative.

## Error response

Errors use the existing envelope:

```json
{
  "type": "error",
  "code": "INVALID_CREDENTIALS",
  "message": "The account ID or password is incorrect."
}
```

Possible `code` values are:

- `ACCOUNT_ID_MISSING`, `ACCOUNT_ID_NOT_STRING`
- `PASSWORD_MISSING`, `PASSWORD_NOT_STRING`
- `INVALID_CREDENTIALS`
- `ALREADY_AUTHENTICATED`
- `ACCOUNT_OPERATION_NOT_AVAILABLE`
- `ACCOUNT_REQUEST_PENDING`
- `LOGIN_FAILED` for an unexpected database or hashing failure

The server does not disclose whether an account ID exists. Passwords and raw
login request payloads must not be written to logs.
