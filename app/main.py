"""FastAPI application: the WebSocket endpoint and the message dispatcher.

A client connects to ``/ws`` first and lands in the **lobby**; rooms are
entered with explicit messages:

```text
connect -> connected + room_list -> create_room / join_room -> game -> leave_room
```

Every game decision is delegated to :class:`~app.room.GameRoom` /
:class:`~app.game.GomokuGame`, and the room registry to
:class:`~app.room_manager.RoomManager`.  This module only translates between
the wire protocol and those layers, and it is the layer that must never
crash on bad client input.
"""

from __future__ import annotations

import logging
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import time
from typing import Any, Optional

import anyio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from . import protocol
from .account_repository import AccountRepository
from .board import Color
from .chat import CHAT_RATE_LIMIT_MAX_MESSAGES, CHAT_RATE_LIMIT_WINDOW_SECONDS
from .config import ServerConfig
from .connection import ClientSession, ConnectionManager
from .database import Database
from .errors import (
    AccountOperationNotAvailableError,
    AccountRequestPendingError,
    AlreadyAuthenticatedError,
    AlreadyInRoomError,
    AuthenticationRequiredError,
    ChatNotAvailableError,
    ChatRateLimitedError,
    ForbiddenMoveError,
    GameError,
    InvalidCredentialsError,
    NotInRoomError,
    TurnExpiredError,
)
from .game import GameOverReason
from .log import configure_logging
from .protocol import (
    ClientCommand,
    BecomeObserverCommand,
    BecomePlayerCommand,
    CreateAccountCommand,
    CreateRoomCommand,
    ErrorCode,
    GetRoomListCommand,
    JoinRoomCommand,
    LeaveRoomCommand,
    LoginCommand,
    MoveCommand,
    PingCommand,
    ProtocolError,
    ReadyCommand,
    ResignCommand,
    ChatCommand,
    UndoRequestCommand,
    UndoResponseCommand,
)
from .room import MAX_PLAYERS, MAX_ROOM_MEMBERS, GameRoom, JoinResult, Member, MemberRole
from .room_manager import RoomManager

logger = logging.getLogger("gomoku")


@dataclass(frozen=True)
class Gateway:
    """The two registries every handler needs.

    ``manager`` answers "who is connected and how do I reach them",
    ``rooms`` answers "which rooms exist and who may enter them".
    """

    manager: ConnectionManager
    rooms: RoomManager
    accounts: AccountRepository
    timer_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)


def create_app(config: Optional[ServerConfig] = None) -> FastAPI:
    """Build the FastAPI app with its own connection and room registries.

    ``config`` carries the operator choices (board size, win length); the
    registries live on ``app.state`` rather than in module level globals,
    so that every application instance is isolated.
    """
    configure_logging()
    server_config = config or ServerConfig()
    manager = ConnectionManager()
    rooms = RoomManager(server_config.game_settings)
    accounts = AccountRepository(Database(server_config.account_db_path))
    gateway = Gateway(
        manager=manager, rooms=rooms, accounts=accounts, timer_tasks={}
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await anyio.to_thread.run_sync(accounts.initialize)
        yield

    app = FastAPI(
        title="Gomoku Realtime Server", version="1.3.0", lifespan=lifespan
    )

    app.state.config = server_config
    app.state.manager = manager
    app.state.rooms = rooms
    app.state.accounts = accounts
    app.state.gateway = gateway

    @app.get("/")
    async def index() -> dict[str, Any]:
        return {
            "name": "Gomoku Realtime Server",
            "board_size": server_config.board_size,
            "win_length": server_config.win_length,
            "rule": server_config.rule_name,
            "starting_color": server_config.starting_color.value,
            "max_players": MAX_PLAYERS,
            "max_room_members": MAX_ROOM_MEMBERS,
            "websocket": "/ws",
        }

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "board_size": server_config.board_size,
            "rooms": rooms.get_room_count(),
            "connections": manager.get_connection_count(),
            "lobby": manager.get_lobby_count(),
            "players": rooms.get_player_count(),
            "room_members": rooms.get_member_count(),
        }

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        session = await manager.connect(websocket)
        try:
            await manager.send(session, protocol.connected(rooms.settings))
            await _send_room_list(gateway, session)
            await _message_loop(gateway, session, websocket)
        except WebSocketDisconnect:
            logger.debug("connection %s socket closed", session.short_id)
        finally:
            # Test clients and ASGI servers may cancel the endpoint task as
            # soon as the peer closes.  Room cleanup and the notifications to
            # the remaining player/lobby must still finish in that case.
            with anyio.CancelScope(shield=True):
                await _handle_disconnect(gateway, session)

    return app


# ----------------------------------------------------------------------
# receiving
# ----------------------------------------------------------------------
async def _message_loop(
    gateway: Gateway, session: ClientSession, websocket: WebSocket
) -> None:
    """Read frames until the socket closes, handling each one in isolation."""
    while True:
        raw = await _receive_text(websocket)
        if raw is None:
            await _send_error(
                gateway,
                session,
                ErrorCode.INVALID_MESSAGE,
                "Only UTF-8 text frames are supported.",
            )
            continue
        await handle_client_message(gateway, session, raw)


async def handle_client_message(
    gateway: Gateway, session: ClientSession, raw: str
) -> None:
    """Parse and execute a single client frame.

    Malformed input results in an ``error`` message for that socket only; it
    never propagates out of this function and never affects other rooms.
    """
    try:
        command: ClientCommand = protocol.parse_client_message(raw)
    except ProtocolError as exc:
        await _send_error(gateway, session, exc.code, exc.message)
        return

    if isinstance(command, PingCommand):
        await gateway.manager.send(session, protocol.pong())
    elif isinstance(command, CreateAccountCommand):
        await _handle_create_account(gateway, session, command)
    elif isinstance(command, LoginCommand):
        await _handle_login(gateway, session, command)
    elif isinstance(command, GetRoomListCommand):
        await _send_room_list(gateway, session)
    elif isinstance(command, CreateRoomCommand):
        await _handle_create_room(gateway, session, command)
    elif isinstance(command, JoinRoomCommand):
        await _handle_join_room(gateway, session, command)
    elif isinstance(command, LeaveRoomCommand):
        await _handle_leave_room(gateway, session)
    elif isinstance(command, BecomePlayerCommand):
        await _handle_become_player(gateway, session)
    elif isinstance(command, BecomeObserverCommand):
        await _handle_become_observer(gateway, session)
    elif isinstance(command, MoveCommand):
        await _handle_move(gateway, session, command)
    elif isinstance(command, ReadyCommand):
        await _handle_ready(gateway, session)
    elif isinstance(command, UndoRequestCommand):
        await _handle_undo_request(gateway, session)
    elif isinstance(command, UndoResponseCommand):
        await _handle_undo_response(gateway, session, command)
    elif isinstance(command, ResignCommand):
        await _handle_resign(gateway, session)
    elif isinstance(command, ChatCommand):
        await _handle_chat(gateway, session, command)


# ----------------------------------------------------------------------
# lobby
# ----------------------------------------------------------------------
async def _handle_create_account(
    gateway: Gateway, session: ClientSession, command: CreateAccountCommand
) -> None:
    try:
        if session.account_id is not None:
            raise AlreadyAuthenticatedError()
        if session.in_room:
            raise AccountOperationNotAvailableError()
        if session.account_request_pending:
            raise AccountRequestPendingError()

        session.account_request_pending = True
        account = await anyio.to_thread.run_sync(
            gateway.accounts.create_account,
            command.account_id,
            command.password,
            command.nickname,
        )
    except GameError as exc:
        logger.info(
            "[ACCOUNT] connection %s create_account rejected: %s",
            session.short_id,
            exc.code,
        )
        await _send_error(gateway, session, exc.code, exc.message)
        return
    except Exception:
        logger.exception(
            "[ACCOUNT] connection %s create_account failed", session.short_id
        )
        await _send_error(
            gateway,
            session,
            ErrorCode.ACCOUNT_CREATE_FAILED,
            "The account could not be created.",
        )
        return
    finally:
        session.account_request_pending = False

    await gateway.manager.send(
        session, protocol.account_created(account.account_id, account.nickname)
    )


async def _handle_login(
    gateway: Gateway, session: ClientSession, command: LoginCommand
) -> None:
    try:
        if session.account_id is not None:
            raise AlreadyAuthenticatedError()
        if session.in_room:
            raise AccountOperationNotAvailableError()
        if session.account_request_pending:
            raise AccountRequestPendingError()

        session.account_request_pending = True
        account = await anyio.to_thread.run_sync(
            gateway.accounts.authenticate,
            command.account_id,
            command.password,
        )
        if account is None:
            raise InvalidCredentialsError()
    except GameError as exc:
        logger.info(
            "[ACCOUNT] connection %s login rejected: %s",
            session.short_id,
            exc.code,
        )
        await _send_error(gateway, session, exc.code, exc.message)
        return
    except Exception:
        logger.exception("[ACCOUNT] connection %s login failed", session.short_id)
        await _send_error(
            gateway,
            session,
            ErrorCode.LOGIN_FAILED,
            "Login could not be completed.",
        )
        return
    finally:
        session.account_request_pending = False

    session.account_id = account.account_id
    session.account_nickname = account.nickname
    await gateway.manager.send(
        session, protocol.login_succeeded(account.account_id, account.nickname)
    )


async def _send_room_list(gateway: Gateway, session: ClientSession) -> None:
    await gateway.manager.send(
        session, protocol.room_list(gateway.rooms.get_room_list())
    )


async def _broadcast_room_list(gateway: Gateway) -> None:
    """Push the current room list to every lobby client.

    Called on every event that changes the list, so clients never poll.
    """
    await gateway.manager.broadcast_to_lobby(
        protocol.room_list(gateway.rooms.get_room_list())
    )


async def _handle_create_room(
    gateway: Gateway, session: ClientSession, command: CreateRoomCommand
) -> None:
    """Create a room owned by the server and seat its creator."""
    try:
        _reject_if_in_room(session)
        _require_authenticated_account(session)
        room, join = await gateway.rooms.create_room(
            session.connection_id,
            command.room_name,
            command.game_type,
            command.turn_time_limit_sec,
        )
    except GameError as exc:
        logger.info(
            "[LOBBY] connection %s create_room rejected: %s",
            session.short_id,
            exc.code,
        )
        await _send_error(gateway, session, exc.code, exc.message)
        return
    except Exception:
        logger.exception(
            "[LOBBY] connection %s create_room failed", session.short_id
        )
        await _send_error(
            gateway,
            session,
            ErrorCode.CREATE_ROOM_FAILED,
            "The room could not be created.",
        )
        return

    session.enter_room(room.room_id)
    await gateway.manager.send(
        session,
        protocol.room_created(room.room_id, room.room_name, room.game_type),
    )
    await _announce_join(gateway, room, join)
    await _broadcast_room_list(gateway)


async def _handle_join_room(
    gateway: Gateway, session: ClientSession, command: JoinRoomCommand
) -> None:
    """Enter an existing room; the session stays in the lobby on failure."""
    try:
        _reject_if_in_room(session)
        _require_authenticated_account(session)
        room, join = await gateway.rooms.join_room(
            command.room_id, session.connection_id
        )
    except GameError as exc:
        logger.info(
            "[LOBBY] connection %s join_room %s rejected: %s",
            session.short_id,
            command.room_id,
            exc.code,
        )
        await _send_error(gateway, session, exc.code, exc.message)
        return

    session.enter_room(room.room_id)
    await _announce_join(gateway, room, join)
    await _broadcast_room_list(gateway)


async def _handle_leave_room(gateway: Gateway, session: ClientSession) -> None:
    """Return a client to the lobby, keeping or removing the room."""
    room_id = session.room_id
    if room_id is None:
        await _send_error(
            gateway,
            session,
            ErrorCode.NOT_IN_ROOM,
            NotInRoomError().message,
        )
        return

    result = await gateway.rooms.leave_room(room_id, session.connection_id)
    session.leave_room()

    await gateway.manager.send(session, protocol.left_room(room_id))
    if result is not None and not result.room_removed:
        await _notify_room_after_leave(gateway, result.room, result.member)
        _sync_turn_timer(gateway, result.room)
    elif result is not None:
        task = gateway.timer_tasks.pop(room_id, None)
        if task is not None:
            task.cancel()
    # The leaver is a lobby client again, so this reaches it as well.
    await _broadcast_room_list(gateway)


async def _handle_disconnect(gateway: Gateway, session: ClientSession) -> None:
    """Clean up a closed connection: room seat, session, lobby list."""
    room_id = session.leave_room()
    gateway.manager.disconnect(session.connection_id)

    if room_id is not None:
        result = await gateway.rooms.leave_room(room_id, session.connection_id)
        if result is not None and not result.room_removed:
            await _notify_room_after_leave(gateway, result.room, result.member)
            _sync_turn_timer(gateway, result.room)
        elif result is not None:
            task = gateway.timer_tasks.pop(room_id, None)
            if task is not None:
                task.cancel()

    await _broadcast_room_list(gateway)


async def _notify_room_after_leave(
    gateway: Gateway, room: GameRoom, member: Member
) -> None:
    """Tell the remaining player that the seat is free again.

    The interrupted round is discarded, so the cleared state follows the
    notification.
    """
    if member.role is MemberRole.PLAYER and member.color is not None:
        await gateway.manager.broadcast_to_room(
            room, protocol.player_disconnected(member.color)
        )
        await gateway.manager.broadcast_to_room(room, _game_state(room))
    await gateway.manager.broadcast_to_room(room, _room_members(gateway, room))


async def _announce_join(
    gateway: Gateway, room: GameRoom, join: JoinResult
) -> None:
    """Send the join handshake; starting requires both players to be ready."""
    member = join.member
    await gateway.manager.send_to_connection(
        member.connection_id,
        protocol.joined(
            room.room_id, room.room_name, room.settings
        ),
    )
    await gateway.manager.send_to_connection(
        member.connection_id, _game_state(room)
    )
    await gateway.manager.broadcast_to_room(room, _room_members(gateway, room))


async def _handle_become_player(gateway: Gateway, session: ClientSession) -> None:
    try:
        room = _require_room(gateway, session)
        result = await room.become_player(session.connection_id)
    except GameError as exc:
        await _send_error(gateway, session, exc.code, exc.message)
        return

    await gateway.manager.send(
        session,
        protocol.role_changed(result.member.role.value, result.member.color),
    )
    await gateway.manager.broadcast_to_room(room, _room_members(gateway, room))
    await gateway.manager.broadcast_to_room(room, _game_state(room))
    await _broadcast_room_list(gateway)


async def _handle_become_observer(gateway: Gateway, session: ClientSession) -> None:
    try:
        room = _require_room(gateway, session)
        result = await room.become_observer(session.connection_id)
    except GameError as exc:
        await _send_error(gateway, session, exc.code, exc.message)
        return

    await gateway.manager.send(
        session,
        protocol.role_changed(result.member.role.value, result.member.color),
    )
    await gateway.manager.broadcast_to_room(room, _room_members(gateway, room))
    await gateway.manager.broadcast_to_room(room, _game_state(room))
    await _broadcast_room_list(gateway)


# ----------------------------------------------------------------------
# game
# ----------------------------------------------------------------------
async def _handle_move(
    gateway: Gateway, session: ClientSession, command: MoveCommand
) -> None:
    try:
        room = _require_room(gateway, session)
    except GameError as exc:
        await _send_error(gateway, session, exc.code, exc.message)
        return

    try:
        result = await room.make_move(session.connection_id, command.x, command.y)
    except TurnExpiredError as exc:
        await _broadcast_turn_timeout(gateway, room, exc.timed_out_color)
        await _send_error(gateway, session, exc.code, exc.message)
        return
    except ForbiddenMoveError as exc:
        # Nothing changed: the stone was never placed and it is still the
        # same player to move, so only the mover hears about it.
        logger.info(
            "[ROOM %s] %s FORBIDDEN %s at (%d, %d) rejected",
            room.room_id,
            _color_label(room, session),
            exc.forbidden_type.value,
            exc.x,
            exc.y,
        )
        color = room.color_of(session.connection_id) or room.starting_color
        await gateway.manager.send(
            session, protocol.forbidden_move_error(color, exc)
        )
        return
    except GameError as exc:
        logger.info(
            "[ROOM %s] %s MOVE (%s, %s) rejected: %s",
            room.room_id,
            _color_label(room, session),
            command.x,
            command.y,
            exc.code,
        )
        await _send_error(gateway, session, exc.code, exc.message)
        return

    logger.info(
        "[ROOM %s] %s MOVE (%d, %d)",
        room.room_id,
        result.color.value,
        result.x,
        result.y,
    )
    # The stone is shown first, the result right after it.
    await gateway.manager.broadcast_to_room(room, protocol.move_result(result))

    # Othello flips and passes, Gomoku forbidden points: both are easiest for
    # clients to reconcile from an authoritative snapshot after every move.
    await gateway.manager.broadcast_to_room(room, _game_state(room))
    _sync_turn_timer(gateway, room)

    if result.is_game_over:
        if result.reason is GameOverReason.NO_FORBIDDEN_FREE_MOVE:
            logger.info(
                "[ROOM %s] %s STUCK: every free point is forbidden",
                room.room_id,
                result.loser.value if result.loser else "-",
            )
        if result.reason is GameOverReason.DRAW:
            logger.info("[ROOM %s] DRAW", room.room_id)
        elif result.winner is not None:
            logger.info("[ROOM %s] %s WIN", room.room_id, result.winner.value)
        await gateway.manager.broadcast_to_room(room, protocol.game_over(result))
        # The lobby shows this room as FINISHED now.
        await _broadcast_room_list(gateway)


async def _handle_ready(gateway: Gateway, session: ClientSession) -> None:
    try:
        room = _require_room(gateway, session)
        result = await room.set_ready(session.connection_id)
    except GameError as exc:
        await _send_error(gateway, session, exc.code, exc.message)
        return

    logger.info(
        "[ROOM %s] %s READY (%d/%d)",
        room.room_id,
        result.player.color.value,
        result.ready_count,
        result.required,
    )
    await gateway.manager.send(
        session, protocol.ready_confirmed()
    )
    await gateway.manager.broadcast_to_players(
        room,
        protocol.player_ready(result.player.color, result.ready_count, result.required),
        exclude=session.connection_id,
    )
    await gateway.manager.broadcast_to_room(room, _room_members(gateway, room))

    if result.game_started:
        logger.info("[ROOM %s] GAME START", room.room_id)
        if result.colors_swapped and result.previous_loser_color is not None:
            logger.info(
                "[ROOM %s] previous loser %s assigned %s",
                room.room_id,
                result.previous_loser_color.value,
                room.starting_color.value,
            )
        # Colors may have changed, so every client gets its own payload.
        for connection_id in room.connection_ids():
            color = room.color_of(connection_id)
            await gateway.manager.send_to_connection(
                connection_id,
                protocol.game_start(color, room.game.current_turn, room.settings),
            )
        await gateway.manager.broadcast_to_room(room, _game_state(room))
        _sync_turn_timer(gateway, room)
        logger.info(
            "[ROOM %s] %s TO MOVE", room.room_id, room.starting_color.value
        )
        # The lobby shows this room as PLAYING.
        await _broadcast_room_list(gateway)


async def _handle_undo_request(
    gateway: Gateway, session: ClientSession
) -> None:
    try:
        room = _require_room(gateway, session)
        result = await room.request_undo(session.connection_id)
    except TurnExpiredError as exc:
        await _broadcast_turn_timeout(gateway, room, exc.timed_out_color)
        await _send_error(gateway, session, exc.code, exc.message)
        return
    except GameError as exc:
        await _send_error(gateway, session, exc.code, exc.message)
        return

    logger.info(
        "[ROOM %s] %s requested undo (%d move%s)",
        room.room_id,
        result.requester.color.value,
        result.undo_count,
        "" if result.undo_count == 1 else "s",
    )
    await gateway.manager.broadcast_to_players(
        room,
        protocol.undo_requested(result.requester.color, result.undo_count),
    )
    _sync_turn_timer(gateway, room)


async def _handle_resign(gateway: Gateway, session: ClientSession) -> None:
    try:
        room = _require_room(gateway, session)
        result = await room.resign(session.connection_id)
    except TurnExpiredError as exc:
        await _broadcast_turn_timeout(gateway, room, exc.timed_out_color)
        await _send_error(gateway, session, exc.code, exc.message)
        return
    except GameError as exc:
        await _send_error(gateway, session, exc.code, exc.message)
        return

    logger.info(
        "[ROOM %s] %s RESIGNED",
        room.room_id,
        result.loser.value,
    )
    await gateway.manager.broadcast_to_room(room, protocol.game_over(result))
    await gateway.manager.broadcast_to_room(room, _game_state(room))
    _sync_turn_timer(gateway, room)
    await _broadcast_room_list(gateway)


async def _handle_chat(
    gateway: Gateway, session: ClientSession, command: ChatCommand
) -> None:
    """Relay one chat line to everybody in the sender's room, sender included.

    Nothing is stored: the room is the only scope and the echo is the only
    acknowledgement, so every member renders the same log in the same order.
    """
    try:
        try:
            room = _require_room(gateway, session)
            _, nickname = _require_authenticated_account(session)
        except (NotInRoomError, AuthenticationRequiredError):
            raise ChatNotAvailableError()
        _enforce_chat_rate_limit(session)
    except GameError as exc:
        await _send_error(gateway, session, exc.code, exc.message)
        return

    sent_at_unix_ms = int(time.time() * 1000)
    logger.info(
        "[ROOM %s] chat from %s (%d chars)", room.room_id, nickname, len(command.text)
    )
    await gateway.manager.broadcast_to_room(
        room, protocol.chat_message(nickname, command.text, sent_at_unix_ms)
    )


def _enforce_chat_rate_limit(session: ClientSession) -> None:
    """Allow ``CHAT_RATE_LIMIT_MAX_MESSAGES`` per rolling window per connection."""
    now = time.monotonic()
    window = session.chat_sent_at
    while window and now - window[0] >= CHAT_RATE_LIMIT_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= CHAT_RATE_LIMIT_MAX_MESSAGES:
        raise ChatRateLimitedError()
    window.append(now)


async def _handle_undo_response(
    gateway: Gateway,
    session: ClientSession,
    command: UndoResponseCommand,
) -> None:
    try:
        room = _require_room(gateway, session)
        result = await room.respond_undo(session.connection_id, command.accepted)
    except GameError as exc:
        await _send_error(gateway, session, exc.code, exc.message)
        return

    logger.info(
        "[ROOM %s] undo request %s by %s",
        room.room_id,
        "accepted" if result.accepted else "rejected",
        _color_label(room, session),
    )
    undone = tuple((move.x, move.y, move.color) for move in result.undone)
    await gateway.manager.broadcast_to_room(
        room,
        protocol.undo_result(
            result.accepted,
            result.requester_color,
            undone,
            result.current_turn,
        ),
    )
    await gateway.manager.broadcast_to_room(room, _game_state(room))
    _sync_turn_timer(gateway, room)


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _game_state(room: GameRoom) -> dict[str, Any]:
    return protocol.game_state(
        room.game,
        turn_deadline_unix_ms=room.turn_deadline_unix_ms,
        turn_revision=room.turn_revision,
    )


def _room_members(gateway: Gateway, room: GameRoom) -> dict[str, Any]:
    nicknames: dict[str, str] = {}
    for connection_id in room.connection_ids():
        member_session = gateway.manager.get_session(connection_id)
        nicknames[connection_id] = (
            member_session.account_nickname
            if member_session is not None
            and member_session.account_nickname is not None
            else "Unknown"
        )
    return protocol.room_members(room, nicknames)


def _sync_turn_timer(gateway: Gateway, room: GameRoom) -> None:
    previous = gateway.timer_tasks.pop(room.room_id, None)
    current = asyncio.current_task()
    if previous is not None and previous is not current:
        previous.cancel()
    deadline = room.turn_deadline_monotonic
    revision = room.turn_revision
    if deadline is None:
        return

    async def wait_for_deadline() -> None:
        try:
            await asyncio.sleep(max(0.0, deadline - time.monotonic()))
            result = await room.expire_turn(revision)
            if result is not None:
                await _broadcast_turn_timeout(gateway, room, result.timed_out_color)
            elif room.turn_revision == revision and room.turn_deadline_monotonic:
                _sync_turn_timer(gateway, room)
        except asyncio.CancelledError:
            return
        finally:
            if gateway.timer_tasks.get(room.room_id) is asyncio.current_task():
                gateway.timer_tasks.pop(room.room_id, None)

    gateway.timer_tasks[room.room_id] = asyncio.create_task(wait_for_deadline())


async def _broadcast_turn_timeout(
    gateway: Gateway, room: GameRoom, timed_out_color: Color
) -> None:
    current_turn = room.game.current_turn
    if current_turn is None:
        return
    await gateway.manager.broadcast_to_room(
        room, protocol.turn_timeout(timed_out_color, current_turn)
    )
    await gateway.manager.broadcast_to_room(room, _game_state(room))
    _sync_turn_timer(gateway, room)


def _reject_if_in_room(session: ClientSession) -> None:
    """A client may only be in one room at a time."""
    if session.in_room:
        raise AlreadyInRoomError()


def _require_authenticated_account(session: ClientSession) -> tuple[str, str]:
    if session.account_id is None or session.account_nickname is None:
        raise AuthenticationRequiredError()
    return session.account_id, session.account_nickname


def _require_room(gateway: Gateway, session: ClientSession) -> GameRoom:
    """Resolve the room of a session, or reject the game message."""
    if session.room_id is None:
        raise NotInRoomError()
    room = gateway.rooms.get_room(session.room_id)
    if room is None:
        # The room disappeared; put the session back into the lobby.
        session.leave_room()
        raise NotInRoomError()
    return room


def _color_label(room: GameRoom, session: ClientSession) -> str:
    """Current color of a session, for log lines."""
    color: Optional[Color] = room.color_of(session.connection_id)
    return color.value if color else "-"


async def _send_error(
    gateway: Gateway,
    session: ClientSession,
    code: Any,
    message: str,
) -> None:
    await gateway.manager.send(session, protocol.error(code, message))


async def _receive_text(websocket: WebSocket) -> Optional[str]:
    """Receive one frame as text.

    Returns ``None`` for frames that are not decodable UTF-8 text, and raises
    :class:`WebSocketDisconnect` when the peer goes away.
    """
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    text = message.get("text")
    if text is not None:
        return text
    payload = message.get("bytes")
    if payload is None:
        return None
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return None


#: Module level application object for ``uvicorn app.main:app``.
#: Its configuration comes from the ``GOMOKU_*`` environment variables so
#: that reload workers inherit the board size chosen by the operator.
app = create_app(ServerConfig.from_env())
