"""Tests for ConnectionManager: sessions, message targeting and cleanup."""

from __future__ import annotations

from typing import Any

from starlette.websockets import WebSocketState

from app.board import Color
from app.config import GameSettings
from app.connection import ConnectionManager, ConnectionState
from app.room import GameRoom


class FakeWebSocket:
    """Minimal stand-in for a Starlette WebSocket."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.client_state = WebSocketState.CONNECTED

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent.append(message)


async def test_connect_creates_a_lobby_session() -> None:
    manager = ConnectionManager()
    socket = FakeWebSocket()

    session = await manager.connect(socket)

    assert session.room_id is None
    assert session.in_room is False
    assert session.state is ConnectionState.LOBBY
    assert manager.get_session(session.connection_id) is session
    assert manager.get_connection_count() == 1
    assert manager.get_lobby_count() == 1
    assert manager.get_in_room_count() == 0


async def test_session_enters_and_leaves_a_room() -> None:
    manager = ConnectionManager()
    session = await manager.connect(FakeWebSocket())

    session.enter_room("room_001")
    assert session.state is ConnectionState.IN_ROOM
    assert manager.get_lobby_count() == 0
    assert manager.get_in_room_count() == 1

    assert session.leave_room() == "room_001"
    assert session.room_id is None
    assert manager.get_lobby_count() == 1


async def test_disconnect_forgets_the_session() -> None:
    manager = ConnectionManager()
    session = await manager.connect(FakeWebSocket())

    assert manager.disconnect(session.connection_id) is session

    assert manager.get_connection_count() == 0
    assert manager.get_session(session.connection_id) is None
    # Disconnecting twice is harmless.
    assert manager.disconnect(session.connection_id) is None


async def test_send_reaches_only_the_addressed_session() -> None:
    manager = ConnectionManager()
    first_socket, second_socket = FakeWebSocket(), FakeWebSocket()
    first = await manager.connect(first_socket)
    await manager.connect(second_socket)

    assert await manager.send(first, {"type": "pong"}) is True

    assert first_socket.sent == [{"type": "pong"}]
    assert second_socket.sent == []


async def test_broadcast_to_lobby_skips_sessions_inside_rooms() -> None:
    manager = ConnectionManager()
    lobby_socket, room_socket = FakeWebSocket(), FakeWebSocket()
    await manager.connect(lobby_socket)
    in_room = await manager.connect(room_socket)
    in_room.enter_room("room_001")

    await manager.broadcast_to_lobby({"type": "room_list", "rooms": []})

    assert lobby_socket.sent == [{"type": "room_list", "rooms": []}]
    assert room_socket.sent == []


async def test_broadcast_to_lobby_can_exclude_a_session() -> None:
    manager = ConnectionManager()
    first_socket, second_socket = FakeWebSocket(), FakeWebSocket()
    first = await manager.connect(first_socket)
    await manager.connect(second_socket)

    await manager.broadcast_to_lobby(
        {"type": "room_list", "rooms": []}, exclude=first.connection_id
    )

    assert first_socket.sent == []
    assert len(second_socket.sent) == 1


async def test_broadcast_to_room_reaches_the_seated_players() -> None:
    manager = ConnectionManager()
    white_socket, black_socket, lobby_socket = (
        FakeWebSocket(),
        FakeWebSocket(),
        FakeWebSocket(),
    )
    white = await manager.connect(white_socket)
    black = await manager.connect(black_socket)
    await manager.connect(lobby_socket)

    room = GameRoom.from_settings("room_001", GameSettings())
    await room.join(white.connection_id)
    await room.join(black.connection_id)

    await manager.broadcast_to_room(room, {"type": "pong"})

    assert white_socket.sent == [{"type": "pong"}]
    assert black_socket.sent == [{"type": "pong"}]
    assert lobby_socket.sent == []


async def test_broadcast_to_room_can_exclude_the_sender() -> None:
    manager = ConnectionManager()
    white_socket, black_socket = FakeWebSocket(), FakeWebSocket()
    white = await manager.connect(white_socket)
    black = await manager.connect(black_socket)

    room = GameRoom.from_settings("room_001", GameSettings())
    await room.join(white.connection_id)
    await room.join(black.connection_id)

    await manager.broadcast_to_room(
        room, {"type": "player_joined", "color": "BLACK"}, exclude=black.connection_id
    )

    assert len(white_socket.sent) == 1
    assert black_socket.sent == []


async def test_send_to_player_uses_the_current_color() -> None:
    manager = ConnectionManager()
    white_socket, black_socket = FakeWebSocket(), FakeWebSocket()
    white = await manager.connect(white_socket)
    black = await manager.connect(black_socket)

    room = GameRoom.from_settings("room_001", GameSettings())
    await room.join(white.connection_id)
    await room.join(black.connection_id)

    assert await manager.send_to_player(room, Color.BLACK, {"type": "pong"}) is True

    assert black_socket.sent == [{"type": "pong"}]
    assert white_socket.sent == []


async def test_send_to_a_missing_color_reports_failure() -> None:
    manager = ConnectionManager()
    session = await manager.connect(FakeWebSocket())
    room = GameRoom.from_settings("room_001", GameSettings())
    await room.join(session.connection_id)

    assert await manager.send_to_player(room, Color.BLACK, {"type": "pong"}) is False


async def test_dead_socket_is_dropped_on_send() -> None:
    manager = ConnectionManager()
    socket = FakeWebSocket()
    session = await manager.connect(socket)
    socket.client_state = WebSocketState.DISCONNECTED

    assert await manager.send(session, {"type": "pong"}) is False

    assert manager.get_connection_count() == 0
    assert manager.get_session(session.connection_id) is None


async def test_dead_socket_is_dropped_on_broadcast() -> None:
    manager = ConnectionManager()
    alive_socket, dead_socket = FakeWebSocket(), FakeWebSocket()
    await manager.connect(alive_socket)
    await manager.connect(dead_socket)
    dead_socket.client_state = WebSocketState.DISCONNECTED

    await manager.broadcast_to_lobby({"type": "room_list", "rooms": []})

    assert manager.get_connection_count() == 1
    assert len(alive_socket.sent) == 1
