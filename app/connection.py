"""WebSocket connection registry.

This is the only module that touches WebSocket objects.  It knows *who is
connected* and *who a message should go to*; which rooms exist is
:class:`~app.room_manager.RoomManager`'s business.

Every connection is a :class:`ClientSession` which is either in the lobby
or inside one room:

```text
LOBBY    -> receives room_list broadcasts
IN_ROOM  -> receives the game messages of its room
```
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Optional
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from .board import Color
from .room import GameRoom

logger = logging.getLogger(__name__)

#: Exceptions that mean "this socket is gone" rather than "the server broke".
_DEAD_SOCKET_ERRORS: tuple[type[BaseException], ...] = (
    WebSocketDisconnect,
    RuntimeError,
    ConnectionError,
    OSError,
)


class ConnectionState(str, Enum):
    LOBBY = "LOBBY"
    IN_ROOM = "IN_ROOM"


@dataclass(frozen=True)
class OnlineUserSnapshot:
    """Authenticated connection information safe for read-only GUI display."""

    connection_id: str
    account_id: str
    nickname: str


@dataclass
class ClientSession:
    """One accepted WebSocket connection and where it currently is."""

    connection_id: str
    websocket: WebSocket
    room_id: Optional[str] = None
    account_id: Optional[str] = None
    account_nickname: Optional[str] = None
    account_request_pending: bool = False

    @property
    def state(self) -> ConnectionState:
        return ConnectionState.LOBBY if self.room_id is None else ConnectionState.IN_ROOM

    @property
    def in_room(self) -> bool:
        return self.room_id is not None

    @property
    def short_id(self) -> str:
        """Shortened id for log lines."""
        return self.connection_id[:8]

    def enter_room(self, room_id: str) -> None:
        self.room_id = room_id

    def leave_room(self) -> Optional[str]:
        room_id, self.room_id = self.room_id, None
        return room_id


class ConnectionManager:
    """Tracks live sessions and delivers messages to them."""

    def __init__(self) -> None:
        self._sessions: dict[str, ClientSession] = {}

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------
    @property
    def sessions(self) -> Mapping[str, ClientSession]:
        """Read-only view of the live sessions."""
        return self._sessions

    def get_session(self, connection_id: str) -> Optional[ClientSession]:
        return self._sessions.get(connection_id)

    def lobby_sessions(self) -> list[ClientSession]:
        """Sessions that are currently not inside a room."""
        return [
            session
            for session in list(self._sessions.values())
            if not session.in_room
        ]

    def get_connection_count(self) -> int:
        """Total number of connected clients (safe from another thread)."""
        return len(self._sessions)

    def get_lobby_count(self) -> int:
        return sum(
            1 for session in list(self._sessions.values()) if not session.in_room
        )

    def get_in_room_count(self) -> int:
        return sum(
            1 for session in list(self._sessions.values()) if session.in_room
        )

    def get_online_users(self) -> list[OnlineUserSnapshot]:
        """Return one row per authenticated WebSocket connection."""
        return [
            OnlineUserSnapshot(
                connection_id=session.connection_id,
                account_id=session.account_id,
                nickname=session.account_nickname,
            )
            for session in list(self._sessions.values())
            if session.account_id is not None
            and session.account_nickname is not None
        ]

    async def connect(self, websocket: WebSocket) -> ClientSession:
        """Register an already-accepted WebSocket as a lobby session."""
        session = ClientSession(connection_id=uuid4().hex, websocket=websocket)
        self._sessions[session.connection_id] = session
        logger.info(
            "[LOBBY] connection %s opened (connections=%d)",
            session.short_id,
            len(self._sessions),
        )
        return session

    def disconnect(self, connection_id: str) -> Optional[ClientSession]:
        """Forget a session.  Room cleanup is the caller's job."""
        session = self._sessions.pop(connection_id, None)
        if session is not None:
            logger.info(
                "[LOBBY] connection %s closed (connections=%d)",
                session.short_id,
                len(self._sessions),
            )
        return session

    # ------------------------------------------------------------------
    # sending
    # ------------------------------------------------------------------
    async def send(self, session: ClientSession, message: dict[str, Any]) -> bool:
        """Send one message to a session; ``False`` when the socket is dead."""
        return await self.send_to_connection(session.connection_id, message)

    async def send_to_connection(
        self, connection_id: str, message: dict[str, Any]
    ) -> bool:
        if await self._send(connection_id, message):
            return True
        self._drop_dead(connection_id)
        return False

    async def send_to_player(
        self, room: GameRoom, color: Color, message: dict[str, Any]
    ) -> bool:
        """Send to whoever holds ``color`` in the current round."""
        connection_id = room.connection_for(color)
        if connection_id is None:
            return False
        return await self.send_to_connection(connection_id, message)

    async def broadcast_to_room(
        self,
        room: GameRoom,
        message: dict[str, Any],
        exclude: Optional[str] = None,
    ) -> None:
        """Send ``message`` to every player and observer in ``room``."""
        targets = [
            connection_id
            for connection_id in room.connection_ids()
            if connection_id != exclude
        ]
        await self._send_many(targets, message)

    async def broadcast_to_players(
        self,
        room: GameRoom,
        message: dict[str, Any],
        exclude: Optional[str] = None,
    ) -> None:
        targets = [
            connection_id
            for connection_id in room.player_connection_ids()
            if connection_id != exclude
        ]
        await self._send_many(targets, message)

    async def broadcast_to_lobby(
        self, message: dict[str, Any], exclude: Optional[str] = None
    ) -> None:
        """Send ``message`` to every session that is not inside a room."""
        targets = [
            session.connection_id
            for session in self.lobby_sessions()
            if session.connection_id != exclude
        ]
        await self._send_many(targets, message)

    async def _send_many(
        self, targets: Iterable[str], message: dict[str, Any]
    ) -> None:
        connection_ids = list(targets)
        if not connection_ids:
            return
        results = await asyncio.gather(
            *(self._send(connection_id, message) for connection_id in connection_ids)
        )
        for connection_id, delivered in zip(connection_ids, results):
            if not delivered:
                self._drop_dead(connection_id)

    async def _send(self, connection_id: str, message: dict[str, Any]) -> bool:
        """Low level send. Returns ``False`` if the socket is gone."""
        session = self._sessions.get(connection_id)
        if session is None:
            return False
        if session.websocket.client_state is not WebSocketState.CONNECTED:
            return False
        try:
            await session.websocket.send_json(message)
        except _DEAD_SOCKET_ERRORS as exc:
            logger.debug("send failed for %s: %r", connection_id, exc)
            return False
        return True

    def _drop_dead(self, connection_id: str) -> None:
        """Forget an unusable socket.

        The endpoint task that owns the socket sees the failure on its next
        ``receive()`` and then runs the normal disconnect flow (leaving the
        room and refreshing the lobby), so no room cleanup happens here.
        """
        session = self._sessions.pop(connection_id, None)
        if session is not None:
            logger.warning(
                "[LOBBY] connection %s dropped (dead socket)", session.short_id
            )
