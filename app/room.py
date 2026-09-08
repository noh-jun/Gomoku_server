"""A single game room: player seats, color assignment and the game lock.

The room owns the authoritative :class:`~app.game.GomokuGame` and serialises
every state change through an :class:`asyncio.Lock`, so that two players
sending messages at the same time can never produce an illegal move or a
half applied result.

Player identity and stone color are kept apart:

* a player is identified by its opaque ``connection_id``
* ``player_colors`` maps that identity to the color of the *current* round

After a finished round the loser is given the starting color (WHITE by
default), so colors are not fixed to a player for the lifetime of the room.
The room never touches a WebSocket; that is
:class:`~app.connection.ConnectionManager`'s job.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Union

from .board import Color
from .config import (
    DEFAULT_BOARD_SIZE,
    DEFAULT_STARTING_COLOR,
    DEFAULT_WIN_LENGTH,
    GameSettings,
)
from .errors import (
    AlreadyObserverError,
    AlreadyPlayerError,
    GameNotStartedError,
    NotInRoomError,
    NotUndoResponderError,
    PlayerRequiredError,
    PlayerSlotsFullError,
    ReadyNotAvailableError,
    ReadyRequiresTwoPlayersError,
    ResignNotAvailableError,
    RoleChangeNotAvailableError,
    RoomFullError,
    UndoAlreadyPendingError,
    UndoNotAvailableError,
    UndoPendingError,
    UnsupportedGameOperationError,
    TurnExpiredError,
)
from .game import GameStatus, GomokuGame, GomokuMove, MoveResult, ResignResult
from .game_type import GameType
from .othello import OthelloGame

GameEngine = Union[GomokuGame, OthelloGame]

#: A room has two game seats and up to 99 total members.
MAX_PLAYERS: int = 2
MAX_ROOM_MEMBERS: int = 99


class MemberRole(str, Enum):
    PLAYER = "PLAYER"
    OBSERVER = "OBSERVER"


@dataclass(frozen=True)
class Player:
    """Snapshot of one seat: who it is and the color it holds right now."""

    connection_id: str
    color: Color


@dataclass(frozen=True)
class Member:
    connection_id: str
    role: MemberRole
    color: Optional[Color] = None


@dataclass(frozen=True)
class RoomSummary:
    """What the lobby is allowed to know about a room.

    Deliberately a plain value object: WebSocket objects, locks and the
    board never leave the room through this.
    """

    room_id: str
    room_name: str
    board_size: int
    win_length: Optional[int]
    players: int
    max_players: int
    player_count: int
    max_game_players: int
    members: int
    max_members: int
    observers: int
    status: GameStatus
    game_type: GameType = GameType.GOMOKU
    turn_time_limit_sec: Optional[int] = None


@dataclass(frozen=True)
class TurnTimeoutResult:
    timed_out_color: Color
    current_turn: Color


@dataclass(frozen=True)
class JoinResult:
    member: Member


@dataclass(frozen=True)
class RoleChangeResult:
    member: Member
    ready_cleared: bool = False


@dataclass(frozen=True)
class ReadyResult:
    """Outcome of a ready vote for the first or a later round.

    ``colors`` is the color assignment for the next round, keyed by
    ``connection_id``; the network layer uses it to tell every client its
    new ``your_color``.  ``player`` is the requester as it was *before* a
    possible color swap.
    """

    player: Player
    game_started: bool
    ready_count: int
    required: int
    colors: dict[str, Color] = field(default_factory=dict)
    previous_loser_color: Optional[Color] = None
    colors_swapped: bool = False


@dataclass(frozen=True)
class PendingUndo:
    requester_id: str
    responder_id: str
    requester_color: Color
    requested_move_count: int
    undo_count: int


@dataclass(frozen=True)
class UndoRequestResult:
    requester: Player
    responder_id: str
    undo_count: int


@dataclass(frozen=True)
class UndoResponseResult:
    accepted: bool
    requester_color: Color
    undone: tuple[GomokuMove, ...]
    current_turn: Optional[Color]


class GameRoom:
    """Owns one match and guarantees atomic state transitions.

    The board geometry and the starting color are captured when the room is
    created and never change afterwards - not between rounds, and not when the
    server configuration is changed for future rooms.
    """

    def __init__(
        self,
        room_id: str,
        room_name: Optional[str] = None,
        board_size: int = DEFAULT_BOARD_SIZE,
        win_length: Optional[int] = DEFAULT_WIN_LENGTH,
        starting_color: Color = DEFAULT_STARTING_COLOR,
        game_type: GameType = GameType.GOMOKU,
        turn_time_limit_sec: Optional[int] = None,
    ) -> None:
        self.room_id: str = room_id
        self.room_name: str = room_name if room_name is not None else room_id
        self.board_size: int = board_size
        self.game_type: GameType = game_type
        self.win_length: Optional[int] = win_length
        self.starting_color: Color = starting_color
        self.turn_time_limit_sec: Optional[int] = turn_time_limit_sec
        self.turn_deadline_monotonic: Optional[float] = None
        self.turn_deadline_unix_ms: Optional[int] = None
        self.turn_revision: int = 0
        self.paused_turn_remaining_sec: Optional[float] = None
        #: connection_id -> color of the current round (insertion = join order)
        self.player_colors: dict[str, Color] = {}
        self.members: set[str] = set()
        self.member_order: list[str] = []
        self.observers: set[str] = set()
        # The room starts empty, so the game is not playable yet.
        self.game: GameEngine
        if game_type is GameType.OTHELLO:
            settings = GameSettings(
                board_size=board_size,
                win_length=win_length,
                starting_color=starting_color,
                game_type=game_type,
            )
            self.game = OthelloGame.from_settings(settings, status=GameStatus.WAITING)
        else:
            if win_length is None:
                raise ValueError("Gomoku win_length is required.")
            self.game = GomokuGame(
                board_size=board_size,
                win_length=win_length,
                starting_color=starting_color,
                status=GameStatus.WAITING,
            )
        self.lock: asyncio.Lock = asyncio.Lock()
        #: connection ids ready to start the first or a later round
        self.ready_connections: set[str] = set()
        self.pending_undo: Optional[PendingUndo] = None

    @classmethod
    def from_settings(
        cls, room_id: str, settings: GameSettings, room_name: Optional[str] = None
    ) -> "GameRoom":
        """Create a room fixed to ``settings`` (as chosen by the server)."""
        return cls(
            room_id,
            room_name=room_name,
            board_size=settings.board_size,
            win_length=settings.win_length,
            starting_color=settings.starting_color,
            game_type=settings.game_type,
            turn_time_limit_sec=settings.turn_time_limit_sec,
        )

    @property
    def settings(self) -> GameSettings:
        """Configuration of this room, safe to send to clients."""
        return GameSettings(
            board_size=self.board_size,
            win_length=self.win_length,
            starting_color=self.starting_color,
            game_type=self.game_type,
            turn_time_limit_sec=self.turn_time_limit_sec,
        )

    # ------------------------------------------------------------------
    # synchronous queries (no await inside, safe on the event loop)
    # ------------------------------------------------------------------
    @property
    def seat_order(self) -> tuple[Color, ...]:
        """Colors handed out to joining players: starting color first."""
        return (self.starting_color, self.starting_color.opponent)

    @property
    def player_count(self) -> int:
        return len(self.player_colors)

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def observer_count(self) -> int:
        return len(self.observers)

    @property
    def lobby_status(self) -> GameStatus:
        """Status as shown in the lobby room list.

        ``WAITING`` while the room is not full (somebody is waiting for an
        opponent), otherwise the status of the running game.
        """
        if not self.players_full:
            return GameStatus.WAITING
        return self.game.status

    def summary(self) -> RoomSummary:
        """Snapshot for the lobby room list."""
        return RoomSummary(
            room_id=self.room_id,
            room_name=self.room_name,
            game_type=self.game_type,
            board_size=self.board_size,
            win_length=self.win_length,
            players=self.member_count,
            max_players=MAX_ROOM_MEMBERS,
            player_count=self.player_count,
            max_game_players=MAX_PLAYERS,
            members=self.member_count,
            max_members=MAX_ROOM_MEMBERS,
            observers=self.observer_count,
            status=self.lobby_status,
            turn_time_limit_sec=self.turn_time_limit_sec,
        )

    def _clear_turn_timer_locked(self) -> None:
        self.turn_revision += 1
        self.turn_deadline_monotonic = None
        self.turn_deadline_unix_ms = None
        self.paused_turn_remaining_sec = None

    def _start_turn_timer_locked(self, duration: Optional[float] = None) -> None:
        self.turn_revision += 1
        if self.game_type is not GameType.GOMOKU or self.turn_time_limit_sec is None:
            self.turn_deadline_monotonic = None
            self.turn_deadline_unix_ms = None
            self.paused_turn_remaining_sec = None
            return
        seconds = float(self.turn_time_limit_sec if duration is None else duration)
        self.turn_deadline_monotonic = time.monotonic() + seconds
        self.turn_deadline_unix_ms = int((time.time() + seconds) * 1000)
        self.paused_turn_remaining_sec = None

    def _expire_turn_locked(self) -> Optional[TurnTimeoutResult]:
        deadline = self.turn_deadline_monotonic
        if deadline is None or time.monotonic() < deadline:
            return None
        if not isinstance(self.game, GomokuGame) or self.game.current_turn is None:
            self._clear_turn_timer_locked()
            return None
        timed_out = self.game.current_turn
        current = self.game.skip_turn(timed_out)
        self._start_turn_timer_locked()
        return TurnTimeoutResult(timed_out, current)

    async def expire_turn(self, revision: int) -> Optional[TurnTimeoutResult]:
        async with self.lock:
            if revision != self.turn_revision:
                return None
            return self._expire_turn_locked()

    @property
    def players_full(self) -> bool:
        return self.player_count >= MAX_PLAYERS

    @property
    def is_full(self) -> bool:
        return self.member_count >= MAX_ROOM_MEMBERS

    @property
    def is_empty(self) -> bool:
        return not self.members

    def color_of(self, connection_id: str) -> Optional[Color]:
        return self.player_colors.get(connection_id)

    def connection_for(self, color: Optional[Color]) -> Optional[str]:
        """The connection currently holding ``color``, if any."""
        if color is None:
            return None
        for connection_id, seat_color in self.player_colors.items():
            if seat_color is color:
                return connection_id
        return None

    def player_for(self, connection_id: str) -> Optional[Player]:
        color = self.player_colors.get(connection_id)
        if color is None:
            return None
        return Player(connection_id=connection_id, color=color)

    def players(self) -> list[Player]:
        """Snapshot of both seats, in join order."""
        return [
            Player(connection_id=connection_id, color=color)
            for connection_id, color in self.player_colors.items()
        ]

    def connection_ids(self) -> list[str]:
        return list(self.member_order)

    def observer_connection_ids(self) -> list[str]:
        return [
            connection_id
            for connection_id in self.member_order
            if connection_id in self.observers
        ]

    def player_connection_ids(self) -> list[str]:
        return list(self.player_colors)

    def member_for(self, connection_id: str) -> Optional[Member]:
        if connection_id not in self.members:
            return None
        color = self.player_colors.get(connection_id)
        role = MemberRole.PLAYER if color is not None else MemberRole.OBSERVER
        return Member(connection_id=connection_id, role=role, color=color)

    def free_color(self) -> Optional[Color]:
        taken = set(self.player_colors.values())
        for color in self.seat_order:
            if color not in taken:
                return color
        return None

    # ------------------------------------------------------------------
    # mutations (always under the room lock)
    # ------------------------------------------------------------------
    async def join(self, connection_id: str) -> JoinResult:
        """Add a connection as an observer without starting the game."""
        async with self.lock:
            if self.is_full:
                raise RoomFullError()
            self.members.add(connection_id)
            self.member_order.append(connection_id)
            self.observers.add(connection_id)
            return JoinResult(
                member=Member(connection_id, MemberRole.OBSERVER)
            )

    async def leave(self, connection_id: str) -> Optional[Member]:
        """Remove a connection from the room.

        Returns the removed :class:`Member`, or ``None`` when the
        connection was not in this room.
        """
        async with self.lock:
            member = self.member_for(connection_id)
            if member is None:
                return None
            was_player = member.role is MemberRole.PLAYER
            self.members.discard(connection_id)
            self.member_order.remove(connection_id)
            self.observers.discard(connection_id)
            self.player_colors.pop(connection_id, None)
            self.ready_connections.discard(connection_id)
            if was_player:
                self.ready_connections.clear()
                self.pending_undo = None
                self.game.reset(status=GameStatus.WAITING)
                self._clear_turn_timer_locked()
            return member

    async def become_player(self, connection_id: str) -> RoleChangeResult:
        async with self.lock:
            member = self.member_for(connection_id)
            if member is None:
                raise NotInRoomError()
            if member.role is MemberRole.PLAYER:
                raise AlreadyPlayerError()
            if self.game.status is GameStatus.PLAYING:
                raise RoleChangeNotAvailableError()
            color = self.free_color()
            if color is None:
                raise PlayerSlotsFullError()
            ready_cleared = bool(self.ready_connections)
            self.ready_connections.clear()
            if self.game.status is GameStatus.FINISHED:
                self.game.reset(status=GameStatus.WAITING)
            self.observers.discard(connection_id)
            self.player_colors[connection_id] = color
            return RoleChangeResult(
                member=Member(connection_id, MemberRole.PLAYER, color),
                ready_cleared=ready_cleared,
            )

    async def become_observer(self, connection_id: str) -> RoleChangeResult:
        async with self.lock:
            member = self.member_for(connection_id)
            if member is None:
                raise NotInRoomError()
            if member.role is MemberRole.OBSERVER:
                raise AlreadyObserverError()
            if self.game.status is GameStatus.PLAYING:
                raise RoleChangeNotAvailableError()
            ready_cleared = bool(self.ready_connections)
            self.ready_connections.clear()
            if self.game.status is GameStatus.FINISHED:
                self.game.reset(status=GameStatus.WAITING)
            self.player_colors.pop(connection_id, None)
            self.observers.add(connection_id)
            return RoleChangeResult(
                member=Member(connection_id, MemberRole.OBSERVER),
                ready_cleared=ready_cleared,
            )

    async def make_move(self, connection_id: str, x: object, y: object) -> MoveResult:
        """Atomically validate, apply and judge a move for one connection.

        Finished-game check, turn check, coordinate check, board mutation,
        Renju judgement, result bookkeeping and the turn switch all happen
        while holding the room lock, so a move that arrives at the same
        moment as a winning move can never be accepted afterwards.
        """
        async with self.lock:
            player = self.player_for(connection_id)
            if player is None:
                if connection_id in self.members:
                    raise PlayerRequiredError()
                raise NotInRoomError()
            if self.pending_undo is not None:
                raise UndoPendingError()
            expired = self._expire_turn_locked()
            if expired is not None:
                raise TurnExpiredError(expired.timed_out_color)
            if not self.players_full and self.game.status is GameStatus.WAITING:
                raise GameNotStartedError("Waiting for the second player.")
            result = self.game.make_move(player.color, x, y)
            if result.is_game_over:
                self.ready_connections.clear()
                self._clear_turn_timer_locked()
            else:
                self._start_turn_timer_locked()
            return result

    async def request_undo(self, connection_id: str) -> UndoRequestResult:
        """Ask the opponent to undo the requester's latest Gomoku move."""
        async with self.lock:
            player = self.player_for(connection_id)
            if player is None:
                if connection_id in self.members:
                    raise PlayerRequiredError()
                raise NotInRoomError()
            if self.game_type is not GameType.GOMOKU or not isinstance(
                self.game, GomokuGame
            ):
                raise UnsupportedGameOperationError(
                    "Undo is supported only for Gomoku."
                )
            if not self.players_full or self.game.status is not GameStatus.PLAYING:
                raise UndoNotAvailableError(
                    "Undo requires a two-player game in progress."
                )
            if self.pending_undo is not None:
                raise UndoAlreadyPendingError()
            if self.ready_connections:
                raise UndoNotAvailableError(
                    "Undo is unavailable while a ready request is pending."
                )
            expired = self._expire_turn_locked()
            if expired is not None:
                raise TurnExpiredError(expired.timed_out_color)

            history = self.game.move_history
            if self.game.current_turn is player.color:
                if (
                    len(history) < 2
                    or history[-1].color is not player.color.opponent
                    or history[-2].color is not player.color
                ):
                    raise UndoNotAvailableError()
                undo_count = 2
            else:
                if not history or history[-1].color is not player.color:
                    raise UndoNotAvailableError()
                undo_count = 1

            responder_id = self.connection_for(player.color.opponent)
            if responder_id is None:
                raise UndoNotAvailableError("The other player is not connected.")
            self.pending_undo = PendingUndo(
                requester_id=connection_id,
                responder_id=responder_id,
                requester_color=player.color,
                requested_move_count=self.game.move_count,
                undo_count=undo_count,
            )
            if self.turn_deadline_monotonic is not None:
                self.paused_turn_remaining_sec = max(
                    0.0, self.turn_deadline_monotonic - time.monotonic()
                )
                self.turn_revision += 1
                self.turn_deadline_monotonic = None
                self.turn_deadline_unix_ms = None
            return UndoRequestResult(
                requester=player,
                responder_id=responder_id,
                undo_count=undo_count,
            )

    async def resign(self, connection_id: str) -> ResignResult:
        """Finish the current game immediately with the requester as loser."""
        async with self.lock:
            player = self.player_for(connection_id)
            if player is None:
                if connection_id in self.members:
                    raise PlayerRequiredError()
                raise NotInRoomError()
            if self.game.status is not GameStatus.PLAYING:
                raise ResignNotAvailableError()
            if self.pending_undo is not None:
                raise UndoPendingError()
            expired = self._expire_turn_locked()
            if expired is not None:
                raise TurnExpiredError(expired.timed_out_color)
            result = self.game.resign(player.color)
            self.ready_connections.clear()
            self._clear_turn_timer_locked()
            return result

    async def respond_undo(
        self, connection_id: str, accepted: bool
    ) -> UndoResponseResult:
        """Accept or reject the pending request; only its opponent may answer."""
        async with self.lock:
            player = self.player_for(connection_id)
            if player is None:
                if connection_id in self.members:
                    raise PlayerRequiredError()
                raise NotInRoomError()
            pending = self.pending_undo
            if pending is None:
                raise UndoNotAvailableError("There is no pending undo request.")
            if connection_id != pending.responder_id:
                raise NotUndoResponderError()
            if not isinstance(self.game, GomokuGame):
                self.pending_undo = None
                raise UnsupportedGameOperationError(
                    "Undo is supported only for Gomoku."
                )
            if self.game.move_count != pending.requested_move_count:
                self.pending_undo = None
                raise UndoNotAvailableError("The game changed after the request.")

            self.pending_undo = None
            if not accepted:
                if self.paused_turn_remaining_sec is not None:
                    self._start_turn_timer_locked(self.paused_turn_remaining_sec)
                return UndoResponseResult(
                    accepted=False,
                    requester_color=pending.requester_color,
                    undone=(),
                    current_turn=self.game.current_turn,
                )

            result = self.game.undo_moves(
                pending.undo_count, next_turn=pending.requester_color
            )
            self._start_turn_timer_locked()
            return UndoResponseResult(
                accepted=True,
                requester_color=pending.requester_color,
                undone=result.undone,
                current_turn=result.current_turn,
            )

    async def set_ready(self, connection_id: str) -> ReadyResult:
        """Record readiness and start a round once both players are ready.

        The color assignment of the next round is decided here, *before*
        the game state is cleared, so the previous loser is still known.
        """
        async with self.lock:
            player = self.player_for(connection_id)
            if player is None:
                if connection_id in self.members:
                    raise PlayerRequiredError()
                raise NotInRoomError()
            if self.pending_undo is not None:
                raise UndoPendingError()
            if not self.players_full:
                raise ReadyRequiresTwoPlayersError()
            if self.game.status is GameStatus.PLAYING:
                raise ReadyNotAvailableError()

            self.ready_connections.add(connection_id)
            if len(self.ready_connections) < MAX_PLAYERS:
                return ReadyResult(
                    player=player,
                    game_started=False,
                    ready_count=len(self.ready_connections),
                    required=MAX_PLAYERS,
                    colors=dict(self.player_colors),
                )

            previous_loser = self.game.loser
            swapped = self._assign_next_round_colors()
            self.game.reset(starting_color=self.starting_color)
            self._start_turn_timer_locked()
            self.ready_connections.clear()
            self.pending_undo = None
            return ReadyResult(
                player=player,
                game_started=True,
                ready_count=MAX_PLAYERS,
                required=MAX_PLAYERS,
                colors=dict(self.player_colors),
                previous_loser_color=previous_loser,
                colors_swapped=swapped,
            )

    def _assign_next_round_colors(self) -> bool:
        """Give the starting color to the loser of the finished round.

        Returns ``True`` when the assignment actually changed.  A draw (or
        an unfinished round) keeps the current colors, as does the case
        where the loser already holds the starting color.
        """
        winner, loser = self.game.winner, self.game.loser
        if winner is None or loser is None:
            return False
        if loser is self.starting_color:
            return False

        loser_connection = self.connection_for(loser)
        winner_connection = self.connection_for(winner)
        if loser_connection is None or winner_connection is None:
            return False

        self.player_colors[loser_connection] = self.starting_color
        self.player_colors[winner_connection] = self.starting_color.opponent
        return True
