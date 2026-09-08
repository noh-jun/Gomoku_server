"""JSON wire protocol: parsing of untrusted client input and message builders.

Nothing in this module touches the game state; it only translates between
JSON documents and typed Python values, and it builds the human readable
result texts that clients may show as-is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Union

from .board import Color, is_integer_coordinate
from .account import normalize_account_id, validate_password, validated_nickname
from .config import GameSettings
from .config import SUPPORTED_TURN_TIME_LIMITS
from .errors import ForbiddenMoveError, GameError
from .game import GameOverReason, MoveResult, ResignResult
from .game_type import GameType, SUPPORTED_GAME_TYPES
from .room import GameEngine, GameRoom, MAX_PLAYERS, MAX_ROOM_MEMBERS, RoomSummary
from .room_name import normalize_room_name
from .rules import ForbiddenType


class ClientMessageType(str, Enum):
    # lobby
    GET_ROOM_LIST = "get_room_list"
    CREATE_ROOM = "create_room"
    JOIN_ROOM = "join_room"
    LEAVE_ROOM = "leave_room"
    BECOME_PLAYER = "become_player"
    BECOME_OBSERVER = "become_observer"
    # game
    MOVE = "move"
    READY = "ready"
    UNDO_REQUEST = "undo_request"
    UNDO_RESPONSE = "undo_response"
    RESIGN = "resign"
    PING = "ping"
    CREATE_ACCOUNT = "create_account"
    LOGIN = "login"


class ServerMessageType(str, Enum):
    CONNECTED = "connected"
    ROOM_LIST = "room_list"
    ROOM_CREATED = "room_created"
    LEFT_ROOM = "left_room"
    JOINED = "joined"
    PLAYER_JOINED = "player_joined"
    GAME_START = "game_start"
    MOVE_RESULT = "move_result"
    GAME_OVER = "game_over"
    GAME_STATE = "game_state"
    PLAYER_DISCONNECTED = "player_disconnected"
    PLAYER_READY = "player_ready"
    READY_CONFIRMED = "ready_confirmed"
    ROLE_CHANGED = "role_changed"
    ROOM_MEMBERS = "room_members"
    UNDO_REQUESTED = "undo_requested"
    UNDO_RESULT = "undo_result"
    TURN_TIMEOUT = "turn_timeout"
    PONG = "pong"
    ACCOUNT_CREATED = "account_created"
    LOGIN_SUCCEEDED = "login_succeeded"
    ERROR = "error"


class ErrorCode(str, Enum):
    INVALID_MESSAGE = "INVALID_MESSAGE"
    UNKNOWN_MESSAGE_TYPE = "UNKNOWN_MESSAGE_TYPE"
    INVALID_MOVE = "INVALID_MOVE"
    NOT_YOUR_TURN = "NOT_YOUR_TURN"
    ROOM_FULL = "ROOM_FULL"
    ROOM_NOT_FOUND = "ROOM_NOT_FOUND"
    NOT_IN_ROOM = "NOT_IN_ROOM"
    ALREADY_IN_ROOM = "ALREADY_IN_ROOM"
    INVALID_ROOM_NAME = "INVALID_ROOM_NAME"
    INVALID_GAME_TYPE = "INVALID_GAME_TYPE"
    ROOM_NAME_TAKEN = "ROOM_NAME_TAKEN"
    CREATE_ROOM_FAILED = "CREATE_ROOM_FAILED"
    GAME_NOT_STARTED = "GAME_NOT_STARTED"
    GAME_ALREADY_FINISHED = "GAME_ALREADY_FINISHED"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    POSITION_OCCUPIED = "POSITION_OCCUPIED"
    FORBIDDEN_MOVE = "FORBIDDEN_MOVE"
    UNDO_NOT_AVAILABLE = "UNDO_NOT_AVAILABLE"
    UNDO_ALREADY_PENDING = "UNDO_ALREADY_PENDING"
    UNDO_PENDING = "UNDO_PENDING"
    NOT_UNDO_RESPONDER = "NOT_UNDO_RESPONDER"
    UNSUPPORTED_GAME_OPERATION = "UNSUPPORTED_GAME_OPERATION"
    READY_NOT_AVAILABLE = "READY_NOT_AVAILABLE"
    READY_REQUIRES_TWO_PLAYERS = "READY_REQUIRES_TWO_PLAYERS"
    PLAYER_REQUIRED = "PLAYER_REQUIRED"
    PLAYER_SLOTS_FULL = "PLAYER_SLOTS_FULL"
    ALREADY_PLAYER = "ALREADY_PLAYER"
    ALREADY_OBSERVER = "ALREADY_OBSERVER"
    ROLE_CHANGE_NOT_AVAILABLE = "ROLE_CHANGE_NOT_AVAILABLE"
    INVALID_TURN_TIME_LIMIT = "INVALID_TURN_TIME_LIMIT"
    UNSUPPORTED_GAME_OPTION = "UNSUPPORTED_GAME_OPTION"
    TURN_EXPIRED = "TURN_EXPIRED"
    ACCOUNT_ID_MISSING = "ACCOUNT_ID_MISSING"
    ACCOUNT_ID_NOT_STRING = "ACCOUNT_ID_NOT_STRING"
    ACCOUNT_ID_EMPTY = "ACCOUNT_ID_EMPTY"
    ACCOUNT_ID_TOO_SHORT = "ACCOUNT_ID_TOO_SHORT"
    ACCOUNT_ID_TOO_LONG = "ACCOUNT_ID_TOO_LONG"
    ACCOUNT_ID_INVALID_CHARACTER = "ACCOUNT_ID_INVALID_CHARACTER"
    ACCOUNT_ID_TAKEN = "ACCOUNT_ID_TAKEN"
    PASSWORD_MISSING = "PASSWORD_MISSING"
    PASSWORD_NOT_STRING = "PASSWORD_NOT_STRING"
    PASSWORD_TOO_SHORT = "PASSWORD_TOO_SHORT"
    PASSWORD_TOO_LONG = "PASSWORD_TOO_LONG"
    PASSWORD_WHITESPACE_NOT_ALLOWED = "PASSWORD_WHITESPACE_NOT_ALLOWED"
    NICKNAME_MISSING = "NICKNAME_MISSING"
    NICKNAME_NOT_STRING = "NICKNAME_NOT_STRING"
    NICKNAME_EMPTY = "NICKNAME_EMPTY"
    NICKNAME_TOO_LONG = "NICKNAME_TOO_LONG"
    NICKNAME_INVALID_CHARACTER = "NICKNAME_INVALID_CHARACTER"
    ALREADY_AUTHENTICATED = "ALREADY_AUTHENTICATED"
    ACCOUNT_OPERATION_NOT_AVAILABLE = "ACCOUNT_OPERATION_NOT_AVAILABLE"
    ACCOUNT_REQUEST_PENDING = "ACCOUNT_REQUEST_PENDING"
    ACCOUNT_CREATE_FAILED = "ACCOUNT_CREATE_FAILED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    LOGIN_FAILED = "LOGIN_FAILED"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"


#: Wording used in the human readable ``message`` of a ``FORBIDDEN_MOVE``
#: rejection.
FORBIDDEN_LABELS: dict[ForbiddenType, str] = {
    ForbiddenType.DOUBLE_THREE: "double-three",
    ForbiddenType.DOUBLE_FOUR: "double-four",
    ForbiddenType.OVERLINE: "overline",
}


class ProtocolError(Exception):
    """Raised when an incoming frame is not a valid protocol message."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code: ErrorCode = code
        self.message: str = message
        super().__init__(message)


# ----------------------------------------------------------------------
# client -> server
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class GetRoomListCommand:
    pass


@dataclass(frozen=True)
class CreateRoomCommand:
    room_name: str
    game_type: GameType = GameType.GOMOKU
    turn_time_limit_sec: Optional[int] = None


@dataclass(frozen=True)
class JoinRoomCommand:
    room_id: str


@dataclass(frozen=True)
class LeaveRoomCommand:
    pass


@dataclass(frozen=True)
class CreateAccountCommand:
    account_id: str
    password: str
    nickname: str


@dataclass(frozen=True)
class LoginCommand:
    account_id: str
    password: str


@dataclass(frozen=True)
class BecomePlayerCommand:
    pass


@dataclass(frozen=True)
class BecomeObserverCommand:
    pass


@dataclass(frozen=True)
class MoveCommand:
    x: int
    y: int


@dataclass(frozen=True)
class ReadyCommand:
    pass


@dataclass(frozen=True)
class UndoRequestCommand:
    pass


@dataclass(frozen=True)
class UndoResponseCommand:
    accepted: bool


@dataclass(frozen=True)
class ResignCommand:
    pass


@dataclass(frozen=True)
class PingCommand:
    pass


ClientCommand = Union[
    CreateAccountCommand,
    LoginCommand,
    GetRoomListCommand,
    CreateRoomCommand,
    JoinRoomCommand,
    LeaveRoomCommand,
    BecomePlayerCommand,
    BecomeObserverCommand,
    MoveCommand,
    ReadyCommand,
    UndoRequestCommand,
    UndoResponseCommand,
    ResignCommand,
    PingCommand,
]


def parse_client_message(raw: str) -> ClientCommand:
    """Parse one raw frame into a typed command.

    Client input is never trusted: colors, turns, board contents and board
    sizes sent by the client are ignored entirely (a ``create_room`` with a
    ``board_size`` uses the server setting), and only ``x``/``y`` of a
    ``move`` plus the ``room_id`` of a ``join_room`` are read - both are
    re-validated by the game and room layers afterwards.
    """
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError:
        raise ProtocolError(ErrorCode.INVALID_MESSAGE, "Message is not valid JSON.")

    if not isinstance(payload, dict):
        raise ProtocolError(
            ErrorCode.INVALID_MESSAGE, "Message must be a JSON object."
        )

    raw_type = payload.get("type")
    if not isinstance(raw_type, str):
        raise ProtocolError(
            ErrorCode.INVALID_MESSAGE, "Message is missing a string 'type' field."
        )

    try:
        message_type = ClientMessageType(raw_type)
    except ValueError:
        raise ProtocolError(
            ErrorCode.UNKNOWN_MESSAGE_TYPE,
            f"Unknown message type: {raw_type!r}.",
        )

    if message_type is ClientMessageType.PING:
        return PingCommand()
    if message_type is ClientMessageType.CREATE_ACCOUNT:
        for field_name, error_code in (
            ("account_id", ErrorCode.ACCOUNT_ID_MISSING),
            ("password", ErrorCode.PASSWORD_MISSING),
            ("nickname", ErrorCode.NICKNAME_MISSING),
        ):
            if field_name not in payload:
                raise ProtocolError(error_code, f"'{field_name}' is required.")
        try:
            account_id = normalize_account_id(payload["account_id"])
            password = validate_password(payload["password"])
            nickname = validated_nickname(payload["nickname"])
        except GameError as exc:
            raise ProtocolError(ErrorCode(exc.code), exc.message)
        return CreateAccountCommand(account_id, password, nickname)
    if message_type is ClientMessageType.LOGIN:
        if "account_id" not in payload:
            raise ProtocolError(
                ErrorCode.ACCOUNT_ID_MISSING, "'account_id' is required."
            )
        if not isinstance(payload["account_id"], str):
            raise ProtocolError(
                ErrorCode.ACCOUNT_ID_NOT_STRING, "'account_id' must be a string."
            )
        if "password" not in payload:
            raise ProtocolError(
                ErrorCode.PASSWORD_MISSING, "'password' is required."
            )
        if not isinstance(payload["password"], str):
            raise ProtocolError(
                ErrorCode.PASSWORD_NOT_STRING, "'password' must be a string."
            )
        try:
            account_id = normalize_account_id(payload["account_id"])
            password = validate_password(payload["password"])
        except GameError:
            raise ProtocolError(
                ErrorCode.INVALID_CREDENTIALS,
                "The account ID or password is incorrect.",
            )
        return LoginCommand(account_id, password)
    if message_type is ClientMessageType.READY:
        return ReadyCommand()
    if message_type is ClientMessageType.BECOME_PLAYER:
        return BecomePlayerCommand()
    if message_type is ClientMessageType.BECOME_OBSERVER:
        return BecomeObserverCommand()
    if message_type is ClientMessageType.UNDO_REQUEST:
        return UndoRequestCommand()
    if message_type is ClientMessageType.RESIGN:
        return ResignCommand()
    if message_type is ClientMessageType.UNDO_RESPONSE:
        accepted = payload.get("accepted")
        if not isinstance(accepted, bool):
            raise ProtocolError(
                ErrorCode.INVALID_MESSAGE,
                "An 'undo_response' message requires a boolean 'accepted' field.",
            )
        return UndoResponseCommand(accepted=accepted)
    if message_type is ClientMessageType.GET_ROOM_LIST:
        return GetRoomListCommand()
    if message_type is ClientMessageType.CREATE_ROOM:
        # Client-supplied board settings and room ids are ignored on purpose.
        try:
            room_name = normalize_room_name(payload.get("room_name"))
        except GameError as exc:
            raise ProtocolError(ErrorCode.INVALID_ROOM_NAME, exc.message)
        raw_game_type = payload.get("game_type", GameType.GOMOKU.value)
        if not isinstance(raw_game_type, str):
            raise ProtocolError(
                ErrorCode.INVALID_GAME_TYPE,
                "'game_type' must be GOMOKU or OTHELLO.",
            )
        try:
            game_type = GameType(raw_game_type.strip().upper())
        except ValueError:
            raise ProtocolError(
                ErrorCode.INVALID_GAME_TYPE,
                "'game_type' must be GOMOKU or OTHELLO.",
            )
        turn_limit = payload.get("turn_time_limit_sec")
        if turn_limit is not None and (
            not isinstance(turn_limit, int)
            or isinstance(turn_limit, bool)
            or turn_limit not in SUPPORTED_TURN_TIME_LIMITS
        ):
            raise ProtocolError(
                ErrorCode.INVALID_TURN_TIME_LIMIT,
                "'turn_time_limit_sec' must be 5, 10, 15, 30, 60, or null.",
            )
        if game_type is GameType.OTHELLO and turn_limit is not None:
            raise ProtocolError(
                ErrorCode.UNSUPPORTED_GAME_OPTION,
                "Othello does not support a turn timer.",
            )
        return CreateRoomCommand(room_name, game_type, turn_limit)
    if message_type is ClientMessageType.LEAVE_ROOM:
        return LeaveRoomCommand()
    if message_type is ClientMessageType.JOIN_ROOM:
        room_id = payload.get("room_id")
        if not isinstance(room_id, str) or not room_id.strip():
            raise ProtocolError(
                ErrorCode.INVALID_MESSAGE,
                "A 'join_room' message requires a non-empty 'room_id'.",
            )
        return JoinRoomCommand(room_id=room_id.strip())

    x, y = payload.get("x"), payload.get("y")
    if not is_integer_coordinate(x) or not is_integer_coordinate(y):
        raise ProtocolError(
            ErrorCode.INVALID_MOVE,
            "A 'move' message requires integer 'x' and 'y' fields.",
        )
    return MoveCommand(x=int(x), y=int(y))  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# server -> client
# ----------------------------------------------------------------------
def settings_fields(settings: GameSettings) -> dict[str, Any]:
    """Configuration fields repeated on every state carrying message."""
    return {
        "game_type": settings.game_type.value,
        "board_size": settings.board_size,
        "win_length": settings.win_length,
        "starting_color": settings.starting_color.value,
        "turn_time_limit_sec": settings.turn_time_limit_sec,
    }


def connected(settings: GameSettings) -> dict[str, Any]:
    """Sent right after the WebSocket is accepted; the client is in the lobby."""
    return {
        "type": ServerMessageType.CONNECTED.value,
        "supported_game_types": [game_type.value for game_type in SUPPORTED_GAME_TYPES],
        "account_creation_supported": True,
        "login_supported": True,
        **settings_fields(settings),
    }


def room_summary(summary: RoomSummary) -> dict[str, Any]:
    return {
        "room_id": summary.room_id,
        "room_name": summary.room_name,
        "game_type": summary.game_type.value,
        "board_size": summary.board_size,
        "win_length": summary.win_length,
        "players": summary.players,
        "max_players": summary.max_players,
        "player_count": summary.player_count,
        "max_game_players": summary.max_game_players,
        "members": summary.members,
        "max_members": summary.max_members,
        "observers": summary.observers,
        "status": summary.status.value,
        "turn_time_limit_sec": summary.turn_time_limit_sec,
    }


def room_list(summaries: Sequence[RoomSummary]) -> dict[str, Any]:
    """The lobby room list; the only room information clients ever get."""
    return {
        "type": ServerMessageType.ROOM_LIST.value,
        "rooms": [room_summary(summary) for summary in summaries],
    }


def room_created(room_id: str, room_name: str, game_type: GameType = GameType.GOMOKU) -> dict[str, Any]:
    return {
        "type": ServerMessageType.ROOM_CREATED.value,
        "room_id": room_id,
        "room_name": room_name,
        "game_type": game_type.value,
    }


def left_room(room_id: str) -> dict[str, Any]:
    return {"type": ServerMessageType.LEFT_ROOM.value, "room_id": room_id}


def joined(
    room_id: str, room_name: str, settings: GameSettings
) -> dict[str, Any]:
    return {
        "type": ServerMessageType.JOINED.value,
        "room_id": room_id,
        "room_name": room_name,
        "your_role": "OBSERVER",
        "your_color": None,
        **settings_fields(settings),
    }


def player_joined(color: Color) -> dict[str, Any]:
    return {"type": ServerMessageType.PLAYER_JOINED.value, "color": color.value}


def role_changed(role: str, color: Optional[Color]) -> dict[str, Any]:
    return {
        "type": ServerMessageType.ROLE_CHANGED.value,
        "your_role": role,
        "your_color": color.value if color else None,
    }


def room_members(
    room: GameRoom, nicknames: Mapping[str, str]
) -> dict[str, Any]:
    return {
        "type": ServerMessageType.ROOM_MEMBERS.value,
        "players": [player.color.value for player in room.players()],
        "ready_colors": [
            player.color.value
            for player in room.players()
            if player.connection_id in room.ready_connections
        ],
        "player_count": room.player_count,
        "max_players": MAX_PLAYERS,
        "observer_count": room.observer_count,
        "member_count": room.member_count,
        "max_members": MAX_ROOM_MEMBERS,
        "player_list": [
            {
                "nickname": nicknames[player.connection_id],
                "color": player.color.value,
                "ready": player.connection_id in room.ready_connections,
            }
            for player in room.players()
        ],
        "observer_list": [
            {"nickname": nicknames[connection_id]}
            for connection_id in room.observer_connection_ids()
        ],
    }


def account_created(account_id: str, nickname: str) -> dict[str, Any]:
    return {
        "type": ServerMessageType.ACCOUNT_CREATED.value,
        "account_id": account_id,
        "nickname": nickname,
    }


def login_succeeded(account_id: str, nickname: str) -> dict[str, Any]:
    return {
        "type": ServerMessageType.LOGIN_SUCCEEDED.value,
        "account_id": account_id,
        "nickname": nickname,
    }


def ready_confirmed(ready: bool = True) -> dict[str, Any]:
    return {
        "type": ServerMessageType.READY_CONFIRMED.value,
        "ready": ready,
    }


def game_start(
    your_color: Optional[Color], current_turn: Optional[Color], settings: GameSettings
) -> dict[str, Any]:
    return {
        "type": ServerMessageType.GAME_START.value,
        "your_role": "PLAYER" if your_color is not None else "OBSERVER",
        "your_color": your_color.value if your_color else None,
        **settings_fields(settings),
        "current_turn": current_turn.value if current_turn else None,
    }


def move_result(result: MoveResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": ServerMessageType.MOVE_RESULT.value,
        "game_type": result.game_type.value,
        "x": result.x,
        "y": result.y,
        "color": result.color.value,
        "next_turn": result.next_turn.value if result.next_turn else None,
    }
    if result.game_type is GameType.OTHELLO:
        payload["flipped"] = [
            {"x": cell.x, "y": cell.y, "color": cell.color.value}
            for cell in result.flipped
        ]
        payload["passed_color"] = (
            result.passed_color.value if result.passed_color else None
        )
    return payload


def describe_game_over(
    winner: Optional[Color],
    loser: Optional[Color],
    reason: Optional[GameOverReason],
) -> str:
    """Default, ready to display result text for the ``message`` field."""
    if reason is GameOverReason.DRAW:
        return "Draw."
    if reason in (GameOverReason.NO_LEGAL_MOVES, GameOverReason.BOARD_FULL):
        if winner is not None:
            return f"{winner.value} wins."
        return "Draw."
    if reason is GameOverReason.NO_FORBIDDEN_FREE_MOVE and loser is not None:
        return f"{loser.value} has no playable point left."
    if winner is not None:
        return f"{winner.value} wins."
    return "Game over."


def game_over(result: Union[MoveResult, ResignResult]) -> dict[str, Any]:
    """Result announcement, broadcast to both players."""
    message = describe_game_over(result.winner, result.loser, result.reason)
    if result.reason is GameOverReason.RESIGNATION:
        message = f"{result.loser.value} resigned. {result.winner.value} wins."
    if result.score is not None:
        black = result.score[Color.BLACK]
        white = result.score[Color.WHITE]
        if result.reason is GameOverReason.RESIGNATION:
            message = f"{result.loser.value} resigned. {result.winner.value} wins."
        elif result.winner is not None and result.loser is not None:
            message = (
                f"{result.winner.value} wins "
                f"{result.score[result.winner]} to {result.score[result.loser]}."
            )
        else:
            message = f"Draw {black} to {white}."
    payload: dict[str, Any] = {
        "type": ServerMessageType.GAME_OVER.value,
        "game_type": result.game_type.value,
        "winner": result.winner.value if result.winner else None,
        "loser": result.loser.value if result.loser else None,
        "reason": result.reason.value if result.reason else None,
        "message": message,
    }
    if result.score is not None:
        payload["score"] = {
            color.value: count for color, count in result.score.items()
        }
    return payload


def point(coordinate: Optional[tuple[int, int]]) -> Optional[dict[str, int]]:
    """One board coordinate as ``{"x": .., "y": ..}``, or ``None``."""
    if coordinate is None:
        return None
    x, y = coordinate
    return {"x": x, "y": y}


def forbidden_moves(
    points: dict[tuple[int, int], ForbiddenType]
) -> list[dict[str, Any]]:
    """Forbidden points of the constrained color, ordered by ``(y, x)``.

    The order is fixed so snapshots stay comparable; clients must not rely
    on it.
    """
    return [
        {"x": x, "y": y, "forbidden_type": kind.value}
        for (x, y), kind in sorted(points.items(), key=lambda item: item[0][::-1])
    ]


def game_state(
    game: GameEngine,
    turn_deadline_unix_ms: Optional[int] = None,
    turn_revision: int = 0,
) -> dict[str, Any]:
    """Full state synchronisation, including the result of a finished game."""
    payload: dict[str, Any] = {
        "type": ServerMessageType.GAME_STATE.value,
        **settings_fields(game.settings),
        "board": game.board_snapshot(),
        "current_turn": game.current_turn.value if game.current_turn else None,
        "winner": game.winner.value if game.winner else None,
        "loser": game.loser.value if game.loser else None,
        "status": game.status.value,
        "game_over_reason": game.reason.value if game.reason else None,
        "last_move": point(game.last_move),
        "turn_deadline_unix_ms": turn_deadline_unix_ms,
        "turn_revision": turn_revision,
    }
    if game.game_type is GameType.OTHELLO:
        payload["score"] = {
            color.value: count for color, count in game.score.items()
        }
        payload["legal_moves"] = [
            {"x": x, "y": y} for x, y in game.legal_moves_for_current_turn
        ]
    else:
        constrained = game.constrained_color
        payload["constrained_color"] = constrained.value if constrained else None
        payload["forbidden_moves"] = forbidden_moves(game.forbidden_moves)
    return payload


def player_disconnected(color: Color) -> dict[str, Any]:
    return {
        "type": ServerMessageType.PLAYER_DISCONNECTED.value,
        "color": color.value,
    }


def player_ready(color: Color, ready_count: int, required: int) -> dict[str, Any]:
    """Notify both clients which player is ready and current progress."""
    return {
        "type": ServerMessageType.PLAYER_READY.value,
        "color": color.value,
        "ready_count": ready_count,
        "required": required,
    }


def undo_requested(requester_color: Color, undo_count: int) -> dict[str, Any]:
    """Notify both players that the opponent's decision is pending."""
    return {
        "type": ServerMessageType.UNDO_REQUESTED.value,
        "game_type": GameType.GOMOKU.value,
        "requester_color": requester_color.value,
        "undo_count": undo_count,
    }


def undo_result(
    accepted: bool,
    requester_color: Color,
    undone: Sequence[tuple[int, int, Color]],
    current_turn: Optional[Color],
) -> dict[str, Any]:
    """Authoritative result of an undo vote."""
    return {
        "type": ServerMessageType.UNDO_RESULT.value,
        "game_type": GameType.GOMOKU.value,
        "accepted": accepted,
        "requester_color": requester_color.value,
        "undone": [
            {"x": x, "y": y, "color": color.value}
            for x, y, color in undone
        ],
        "current_turn": current_turn.value if current_turn else None,
    }


def turn_timeout(timed_out_color: Color, current_turn: Color) -> dict[str, Any]:
    return {
        "type": ServerMessageType.TURN_TIMEOUT.value,
        "game_type": GameType.GOMOKU.value,
        "timed_out_color": timed_out_color.value,
        "current_turn": current_turn.value,
    }


def pong() -> dict[str, Any]:
    return {"type": ServerMessageType.PONG.value}


def error(code: Union[ErrorCode, str], message: str) -> dict[str, Any]:
    return {
        "type": ServerMessageType.ERROR.value,
        "code": code.value if isinstance(code, ErrorCode) else code,
        "message": message,
    }


def forbidden_move_error(color: Color, exc: ForbiddenMoveError) -> dict[str, Any]:
    """Rejection of a Renju forbidden point, sent to the mover only.

    Nothing moved on the server: the board, the turn and the move count are
    exactly what they were before the ``move`` arrived.  The coordinates are
    echoed back so the client can point at the intersection it refused.
    """
    label = FORBIDDEN_LABELS.get(exc.forbidden_type, "forbidden")
    return {
        "type": ServerMessageType.ERROR.value,
        "code": ErrorCode.FORBIDDEN_MOVE.value,
        "message": f"{label.capitalize()} is forbidden for {color.value}.",
        "forbidden_type": exc.forbidden_type.value,
        "x": exc.x,
        "y": exc.y,
    }
