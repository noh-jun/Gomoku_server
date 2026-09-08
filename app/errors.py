"""Domain level errors.

Every error carries a stable ``code`` string.  The network layer maps that
code straight into the ``error`` message of the wire protocol, so the game
logic never has to know about JSON, WebSockets or FastAPI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from .rules import ForbiddenType


class GameError(Exception):
    """Base class for every rejected game/room operation."""

    code: str = "INVALID_MOVE"
    default_message: str = "Invalid move."

    def __init__(self, message: Optional[str] = None) -> None:
        self.message: str = message or self.default_message
        super().__init__(self.message)


class InvalidMoveError(GameError):
    code = "INVALID_MOVE"
    default_message = "Move coordinates must be integers."


class OutOfRangeError(GameError):
    code = "OUT_OF_RANGE"
    default_message = "Coordinates are out of the board range."


class PositionOccupiedError(GameError):
    code = "POSITION_OCCUPIED"
    default_message = "This position is already occupied."


class ForbiddenMoveError(GameError):
    """The constrained player may not put a stone on this point.

    The move is rejected before anything is placed, so the board, the turn
    and the move count are left exactly as they were and the game goes on.
    The forbidden kind and the coordinates travel with the error so that
    clients can point at the offending intersection.
    """

    code = "FORBIDDEN_MOVE"
    default_message = "This point is forbidden for the first player."

    def __init__(
        self,
        forbidden_type: "ForbiddenType",
        x: int,
        y: int,
        message: Optional[str] = None,
    ) -> None:
        self.forbidden_type: "ForbiddenType" = forbidden_type
        self.x: int = x
        self.y: int = y
        super().__init__(message)


class NotYourTurnError(GameError):
    code = "NOT_YOUR_TURN"
    default_message = "It is not your turn."


class TurnExpiredError(GameError):
    code = "TURN_EXPIRED"
    default_message = "The turn time limit expired before this move arrived."

    def __init__(self, timed_out_color: object, message: Optional[str] = None) -> None:
        self.timed_out_color = timed_out_color
        super().__init__(message)


class GameNotStartedError(GameError):
    code = "GAME_NOT_STARTED"
    default_message = "The game has not started yet."


class GameAlreadyFinishedError(GameError):
    code = "GAME_ALREADY_FINISHED"
    default_message = "The game is already finished."


class ReadyNotAvailableError(GameError):
    code = "READY_NOT_AVAILABLE"
    default_message = "Ready is available only before or between games."


class ReadyRequiresTwoPlayersError(GameError):
    code = "READY_REQUIRES_TWO_PLAYERS"
    default_message = "Ready requires two connected players."


class PlayerRequiredError(GameError):
    code = "PLAYER_REQUIRED"
    default_message = "This operation is available only to a player."


class PlayerSlotsFullError(GameError):
    code = "PLAYER_SLOTS_FULL"
    default_message = "Both player slots are already occupied."


class AlreadyPlayerError(GameError):
    code = "ALREADY_PLAYER"
    default_message = "This member is already a player."


class AlreadyObserverError(GameError):
    code = "ALREADY_OBSERVER"
    default_message = "This member is already an observer."


class RoleChangeNotAvailableError(GameError):
    code = "ROLE_CHANGE_NOT_AVAILABLE"
    default_message = "Player roles cannot be changed while a game is in progress."


class UndoNotAvailableError(GameError):
    code = "UNDO_NOT_AVAILABLE"
    default_message = "There is no move available to undo."


class UndoAlreadyPendingError(GameError):
    code = "UNDO_ALREADY_PENDING"
    default_message = "An undo request is already pending."


class UndoPendingError(GameError):
    code = "UNDO_PENDING"
    default_message = "Moves and ready are paused while an undo request is pending."


class NotUndoResponderError(GameError):
    code = "NOT_UNDO_RESPONDER"
    default_message = "Only the other player may answer this undo request."


class UnsupportedGameOperationError(GameError):
    code = "UNSUPPORTED_GAME_OPERATION"
    default_message = "This operation is not supported for the current game."


class ResignNotAvailableError(GameError):
    code = "RESIGN_NOT_AVAILABLE"
    default_message = "Resignation is available only during a game."


class RoomFullError(GameError):
    code = "ROOM_FULL"
    default_message = "This room already has 99 members."


class RoomNotFoundError(GameError):
    code = "ROOM_NOT_FOUND"
    default_message = "The requested room does not exist."


class NotInRoomError(GameError):
    code = "NOT_IN_ROOM"
    default_message = "You are not currently in a room."


class AlreadyInRoomError(GameError):
    code = "ALREADY_IN_ROOM"
    default_message = "Leave the current room before joining another room."


class InvalidRoomNameError(GameError):
    code = "INVALID_ROOM_NAME"
    default_message = "The room name is invalid."


class RoomNameTakenError(GameError):
    code = "ROOM_NAME_TAKEN"
    default_message = "A room with that name already exists."


class InvalidNicknameError(GameError):
    code = "INVALID_NICKNAME"
    default_message = "The nickname is invalid."


class InvalidAccountIdError(GameError):
    code = "INVALID_ACCOUNT_ID"
    default_message = "The account ID is invalid."


class AccountIdMissingError(InvalidAccountIdError):
    code = "ACCOUNT_ID_MISSING"
    default_message = "The account ID is required."


class AccountIdNotStringError(InvalidAccountIdError):
    code = "ACCOUNT_ID_NOT_STRING"
    default_message = "The account ID must be a string."


class AccountIdEmptyError(InvalidAccountIdError):
    code = "ACCOUNT_ID_EMPTY"
    default_message = "The account ID must not be empty."


class AccountIdTooShortError(InvalidAccountIdError):
    code = "ACCOUNT_ID_TOO_SHORT"
    default_message = "The account ID must contain at least 4 letters."


class AccountIdTooLongError(InvalidAccountIdError):
    code = "ACCOUNT_ID_TOO_LONG"
    default_message = "The account ID must contain at most 20 letters."


class AccountIdInvalidCharacterError(InvalidAccountIdError):
    code = "ACCOUNT_ID_INVALID_CHARACTER"
    default_message = "The account ID may contain ASCII letters only."


class AccountIdTakenError(GameError):
    code = "ACCOUNT_ID_TAKEN"
    default_message = "The account ID is already in use."


class InvalidPasswordError(GameError):
    code = "INVALID_PASSWORD"
    default_message = "The password is invalid."


class PasswordMissingError(InvalidPasswordError):
    code = "PASSWORD_MISSING"
    default_message = "The password is required."


class PasswordNotStringError(InvalidPasswordError):
    code = "PASSWORD_NOT_STRING"
    default_message = "The password must be a string."


class PasswordTooShortError(InvalidPasswordError):
    code = "PASSWORD_TOO_SHORT"
    default_message = "The password must contain at least 4 characters."


class PasswordTooLongError(InvalidPasswordError):
    code = "PASSWORD_TOO_LONG"
    default_message = "The password must contain at most 20 characters."


class PasswordWhitespaceError(InvalidPasswordError):
    code = "PASSWORD_WHITESPACE_NOT_ALLOWED"
    default_message = "The password must not contain whitespace."


class NicknameMissingError(InvalidNicknameError):
    code = "NICKNAME_MISSING"
    default_message = "The nickname is required."


class NicknameNotStringError(InvalidNicknameError):
    code = "NICKNAME_NOT_STRING"
    default_message = "The nickname must be a string."


class NicknameEmptyError(InvalidNicknameError):
    code = "NICKNAME_EMPTY"
    default_message = "The nickname must not be empty."


class NicknameTooLongError(InvalidNicknameError):
    code = "NICKNAME_TOO_LONG"
    default_message = "The nickname must contain at most 20 characters."


class NicknameInvalidCharacterError(InvalidNicknameError):
    code = "NICKNAME_INVALID_CHARACTER"
    default_message = "The nickname contains a forbidden character."


class ChatNotAvailableError(GameError):
    code = "CHAT_NOT_AVAILABLE"
    default_message = "Chat is only available to authenticated members inside a room."


class ChatTextInvalidError(GameError):
    code = "CHAT_TEXT_INVALID"
    default_message = "Chat text must contain 1 to 200 characters."


class ChatRateLimitedError(GameError):
    code = "CHAT_RATE_LIMITED"
    default_message = "Too many chat messages; wait a moment and try again."


class AlreadyAuthenticatedError(GameError):
    code = "ALREADY_AUTHENTICATED"
    default_message = "This connection is already authenticated."


class AccountOperationNotAvailableError(GameError):
    code = "ACCOUNT_OPERATION_NOT_AVAILABLE"
    default_message = "Account operations are not available inside a room."


class AccountRequestPendingError(GameError):
    code = "ACCOUNT_REQUEST_PENDING"
    default_message = "Another account request is already in progress."


class InvalidCredentialsError(GameError):
    code = "INVALID_CREDENTIALS"
    default_message = "The account ID or password is incorrect."


class AuthenticationRequiredError(GameError):
    code = "AUTHENTICATION_REQUIRED"
    default_message = "Log in before entering a room."
