"""Ownership of the room list.

The server - not the client - owns which rooms exist:

* room ids are generated here, never accepted from a client
* a ``join_room`` for an unknown id fails instead of creating a room
* rooms that lose their last member are removed

The manager only decides *which* room a connection may enter; everything
that happens inside a room (turns, moves, results) stays in
:class:`~app.room.GameRoom`.  Accordingly this module has its own lock for
the room registry, while each room keeps its own game lock.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from .config import GameSettings, OTHELLO_BOARD_SIZE, OTHELLO_STARTING_COLOR
from .errors import RoomNameTakenError, RoomNotFoundError
from .room import GameRoom, JoinResult, Member, RoomSummary
from .room_name import normalize_room_name, room_name_key
from .game_type import GameType

logger = logging.getLogger(__name__)

#: Room ids look like ``room_001``; the counter never reuses a number.
ROOM_ID_PREFIX = "room_"
ROOM_ID_DIGITS = 3


@dataclass(frozen=True)
class LeaveResult:
    """Outcome of a member leaving a room."""

    room: GameRoom
    member: Member
    room_removed: bool


class RoomManager:
    """Creates, finds and removes rooms."""

    def __init__(self, settings: Optional[GameSettings] = None) -> None:
        self._settings: GameSettings = settings or GameSettings()
        self._rooms: dict[str, GameRoom] = {}
        self._room_ids_by_name: dict[str, str] = {}
        self._counter: int = 0
        #: Guards the room registry (creation, joining, removal).
        self.lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # settings
    # ------------------------------------------------------------------
    @property
    def settings(self) -> GameSettings:
        """Settings applied to rooms created from now on."""
        return self._settings

    def update_settings(self, settings: GameSettings) -> None:
        """Change the settings used for *future* rooms.

        Existing rooms - and therefore running games - are never resized.
        """
        self._settings = settings

    # ------------------------------------------------------------------
    # read-only queries (no await: safe to call from the GUI thread)
    # ------------------------------------------------------------------
    def get_room(self, room_id: str) -> Optional[GameRoom]:
        return self._rooms.get(room_id)

    def require_room(self, room_id: str) -> GameRoom:
        room = self._rooms.get(room_id)
        if room is None:
            raise RoomNotFoundError()
        return room

    def get_room_count(self) -> int:
        return len(self._rooms)

    def get_player_count(self) -> int:
        """Players seated in rooms (lobby connections excluded)."""
        return sum(room.player_count for room in list(self._rooms.values()))

    def get_member_count(self) -> int:
        return sum(room.member_count for room in list(self._rooms.values()))

    def get_room_list(self) -> list[RoomSummary]:
        """Snapshot of every room, ordered by room id.

        ``list(...)`` copies the registry in one step, so a snapshot taken
        from another thread (the GUI) can never trip over a mutation.
        """
        return sorted(
            (room.summary() for room in list(self._rooms.values())),
            key=lambda summary: summary.room_id,
        )

    # ------------------------------------------------------------------
    # mutations (under the registry lock)
    # ------------------------------------------------------------------
    def _next_room_id(self) -> str:
        self._counter += 1
        return f"{ROOM_ID_PREFIX}{self._counter:0{ROOM_ID_DIGITS}d}"

    async def create_room(
        self,
        connection_id: str,
        room_name: object,
        game_type: GameType = GameType.GOMOKU,
        turn_time_limit_sec: Optional[int] = None,
    ) -> tuple[GameRoom, JoinResult]:
        """Create a room with the current server settings and seat its creator.

        The creator enters as an observer, so no separate ``join_room`` is needed.
        """
        normalized_name = normalize_room_name(room_name)
        name_key = room_name_key(normalized_name)
        async with self.lock:
            if name_key in self._room_ids_by_name:
                raise RoomNameTakenError()
            room_id = self._next_room_id()
            settings = self._settings
            if game_type is GameType.GOMOKU:
                settings = GameSettings(
                    board_size=settings.board_size,
                    win_length=settings.win_length,
                    starting_color=settings.starting_color,
                    game_type=game_type,
                    turn_time_limit_sec=turn_time_limit_sec,
                )
            if game_type is GameType.OTHELLO:
                settings = GameSettings(
                    game_type=GameType.OTHELLO,
                    board_size=OTHELLO_BOARD_SIZE,
                    win_length=None,
                    starting_color=OTHELLO_STARTING_COLOR,
                    turn_time_limit_sec=turn_time_limit_sec,
                )
            room = GameRoom.from_settings(room_id, settings, room_name=normalized_name)
            logger.info(
                "[ROOM %s] CREATED game=%s board=%d win=%s first=%s",
                room_id,
                room.game_type.value,
                room.board_size,
                room.win_length,
                room.starting_color.value,
            )
            # Publish only after seating succeeds, so no partial room is visible.
            join = await room.join(connection_id)
            try:
                self._rooms[room_id] = room
                self._room_ids_by_name[name_key] = room_id
            except Exception:
                self._rooms.pop(room_id, None)
                self._room_ids_by_name.pop(name_key, None)
                raise
            logger.info("[ROOM %s] observer %s joined", room_id, connection_id[:8])
            return room, join

    async def join_room(
        self, room_id: str, connection_id: str
    ) -> tuple[GameRoom, JoinResult]:
        """Seat a connection in an existing room.

        Raises :class:`app.errors.RoomNotFoundError` for an unknown id and
        :class:`app.errors.RoomFullError` when all 99 member slots are taken; the
        caller stays in the lobby in both cases.
        """
        async with self.lock:
            room = self.require_room(room_id)
            join = await room.join(connection_id)
            logger.info("[ROOM %s] observer %s joined", room_id, connection_id[:8])
            return room, join

    async def leave_room(
        self, room_id: str, connection_id: str
    ) -> Optional[LeaveResult]:
        """Remove a connection from a room, deleting the room when empty.

        Returns ``None`` when the room is gone already or the connection
        was not seated in it.
        """
        async with self.lock:
            room = self._rooms.get(room_id)
            if room is None:
                return None
            member = await room.leave(connection_id)
            if member is None:
                return None

            logger.info("[ROOM %s] %s left", room_id, member.role.value)
            removed = self._remove_if_empty(room)
            return LeaveResult(room=room, member=member, room_removed=removed)

    async def remove_room(self, room_id: str) -> bool:
        """Force a room out of the registry (used by tests and shutdown)."""
        async with self.lock:
            if room_id not in self._rooms:
                return False
            room = self._rooms.pop(room_id)
            self._room_ids_by_name.pop(room_name_key(room.room_name), None)
            logger.info("[ROOM %s] room removed", room_id)
            return True

    def _remove_if_empty(self, room: GameRoom) -> bool:
        """Drop an empty room; caller must hold :attr:`lock`."""
        if not room.is_empty:
            return False
        if self._rooms.get(room.room_id) is not room:
            return False
        del self._rooms[room.room_id]
        self._room_ids_by_name.pop(room_name_key(room.room_name), None)
        logger.info("[ROOM %s] room removed", room.room_id)
        return True
