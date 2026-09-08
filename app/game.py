"""Pure Gomoku/Renju game logic.

This module contains no networking, no asyncio and no framework code, which
makes it directly unit testable and keeps rule changes away from the
transport layer.

Three concepts are kept apart on purpose:

* ``color``          - the stone color (BLACK / WHITE)
* ``starting_color`` - the color that opens the game and carries the Renju
  forbidden moves (WHITE by default here)
* ``current_turn``   - whose turn it is right now, ``None`` once the game is
  finished

Which *player* holds which color is not decided here; that mapping lives in
:class:`~app.room.GameRoom`, which reassigns it after every round.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, cast

from .board import Board, Color, create_board, in_bounds, is_integer_coordinate
from .config import (
    DEFAULT_BOARD_SIZE,
    DEFAULT_STARTING_COLOR,
    DEFAULT_WIN_LENGTH,
    GameSettings,
)
from .game_type import GameType
from .errors import (
    ForbiddenMoveError,
    GameAlreadyFinishedError,
    GameNotStartedError,
    InvalidMoveError,
    NotYourTurnError,
    OutOfRangeError,
    PositionOccupiedError,
    UndoNotAvailableError,
)
from .rules import ForbiddenType, RenjuRule, Rule


class GameStatus(str, Enum):
    WAITING = "WAITING"
    PLAYING = "PLAYING"
    FINISHED = "FINISHED"


class GameOverReason(str, Enum):
    FIVE_IN_A_ROW = "five_in_a_row"
    DRAW = "draw"
    #: The constrained player is to move and every empty point is forbidden.
    #: Forbidden points are blocked rather than punished, so this is the only
    #: way Renju restrictions can still decide a game.
    NO_FORBIDDEN_FREE_MOVE = "no_forbidden_free_move"
    NO_LEGAL_MOVES = "no_legal_moves"
    BOARD_FULL = "board_full"
    RESIGNATION = "resignation"


@dataclass(frozen=True)
class ChangedCell:
    """One board cell changed as a side effect of a move."""

    x: int
    y: int
    color: Color


@dataclass(frozen=True)
class GomokuMove:
    """One accepted move, retained for authoritative undo."""

    x: int
    y: int
    color: Color
    sequence: int


@dataclass(frozen=True)
class UndoResult:
    """Moves removed from newest to oldest and the restored turn."""

    undone: tuple[GomokuMove, ...]
    current_turn: Color


@dataclass(frozen=True)
class ResignResult:
    game_type: GameType
    winner: Color
    loser: Color
    reason: GameOverReason = GameOverReason.RESIGNATION
    score: Optional[dict[Color, int]] = None


@dataclass(frozen=True)
class MoveResult:
    """Outcome of one accepted move.

    ``accepted`` is always ``True`` here: a move that fails validation
    raises a :class:`app.errors.GameError` instead of producing a result.
    A forbidden Renju point is one of those rejections
    (:class:`app.errors.ForbiddenMoveError`), so no ``MoveResult`` ever
    describes a forbidden move.
    """

    x: int
    y: int
    color: Color
    next_turn: Optional[Color]
    status: GameStatus
    winner: Optional[Color] = None
    loser: Optional[Color] = None
    reason: Optional[GameOverReason] = None
    flipped: tuple[ChangedCell, ...] = ()
    passed_color: Optional[Color] = None
    score: Optional[dict[Color, int]] = None
    game_type: GameType = GameType.GOMOKU
    accepted: bool = True

    @property
    def is_game_over(self) -> bool:
        return self.status is GameStatus.FINISHED


class GomokuGame:
    """Authoritative game state for a single match.

    A freshly constructed game is ``PLAYING`` with ``starting_color`` to
    move. A :class:`~app.room.GameRoom` constructs it with
    ``status=GameStatus.WAITING``, so moves are rejected until both connected
    players are ready.

    ``board_size``, ``win_length`` and the rule engine are fixed for the
    lifetime of the object; :meth:`reset` preserves them and may only
    change the starting color.
    """

    def __init__(
        self,
        board_size: int = DEFAULT_BOARD_SIZE,
        win_length: int = DEFAULT_WIN_LENGTH,
        starting_color: Color = DEFAULT_STARTING_COLOR,
        rule: Optional[Rule] = None,
        status: GameStatus = GameStatus.PLAYING,
    ) -> None:
        if board_size < 1:
            raise ValueError("board_size must be positive")
        if win_length < 1:
            raise ValueError("win_length must be positive")
        self.board_size: int = board_size
        # An injected rule owns its own win condition.
        self.rule: Rule = rule if rule is not None else RenjuRule(win_length)
        self.win_length: int = self.rule.win_length
        self.starting_color: Color = starting_color
        self.board: Board = create_board(board_size)
        self.current_turn: Optional[Color] = starting_color
        self.winner: Optional[Color] = None
        self.loser: Optional[Color] = None
        self.status: GameStatus = status
        self.reason: Optional[GameOverReason] = None
        self.move_count: int = 0
        self.last_move: Optional[tuple[int, int]] = None
        self.move_history: list[GomokuMove] = []
        #: Points the constrained color may not play in the current position,
        #: kept in step with the board by :meth:`_refresh_forbidden_moves`.
        self.forbidden_moves: dict[tuple[int, int], ForbiddenType] = {}
        self._refresh_forbidden_moves()

    @classmethod
    def from_settings(
        cls,
        settings: GameSettings,
        rule: Optional[Rule] = None,
        status: GameStatus = GameStatus.PLAYING,
    ) -> "GomokuGame":
        """Build a game from a :class:`~app.config.GameSettings` bundle."""
        if settings.game_type is not GameType.GOMOKU or settings.win_length is None:
            raise ValueError("GomokuGame requires GOMOKU settings.")
        return cls(
            board_size=settings.board_size,
            win_length=settings.win_length,
            starting_color=settings.starting_color,
            rule=rule,
            status=status,
        )

    @property
    def settings(self) -> GameSettings:
        """The configuration of this game, ready to be put on the wire."""
        return GameSettings(
            board_size=self.board_size,
            win_length=self.win_length,
            starting_color=self.starting_color,
            game_type=GameType.GOMOKU,
        )

    @property
    def game_type(self) -> GameType:
        return GameType.GOMOKU

    @property
    def rule_name(self) -> str:
        return self.rule.name

    # ------------------------------------------------------------------
    # state transitions
    # ------------------------------------------------------------------
    def reset(
        self,
        starting_color: Optional[Color] = None,
        status: GameStatus = GameStatus.PLAYING,
    ) -> None:
        """Clear the board and start over.

        The board size, win length and rule engine are preserved.  Passing
        ``starting_color`` changes who opens the next game; leaving it out
        keeps the current starting color.
        """
        if starting_color is not None:
            self.starting_color = starting_color
        self.board = create_board(self.board_size)
        self.current_turn = self.starting_color
        self.winner = None
        self.loser = None
        self.status = status
        self.reason = None
        self.move_count = 0
        self.last_move = None
        self.move_history.clear()
        self._refresh_forbidden_moves()

    def start(self) -> None:
        """Mark the game as playable without touching the board."""
        self.status = GameStatus.PLAYING
        if self.current_turn is None:
            self.current_turn = self.starting_color

    def suspend(self) -> None:
        """Move an unfinished game back to ``WAITING`` (e.g. player left)."""
        if self.status is GameStatus.PLAYING:
            self.status = GameStatus.WAITING

    def _finish(
        self,
        winner: Optional[Color],
        loser: Optional[Color],
        reason: GameOverReason,
    ) -> None:
        """End the game atomically: nobody can move afterwards."""
        self.winner = winner
        self.loser = loser
        self.reason = reason
        self.status = GameStatus.FINISHED
        # No turn belongs to a finished game, so clients cannot show
        # "WHITE TURN" after the result - and no forbidden points belong to
        # one either, so clients stop drawing them.
        self.current_turn = None
        self.forbidden_moves = {}

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    def stone_at(self, x: int, y: int) -> Optional[Color]:
        return self.board[y][x]

    @property
    def constrained_color(self) -> Optional[Color]:
        """The color bound by forbidden moves, ``None`` under free style."""
        return self.rule.constrained_color(self.starting_color)

    def is_constrained(self, color: Color) -> bool:
        """``True`` when Renju forbidden moves apply to ``color``."""
        return color is self.constrained_color

    def _refresh_forbidden_moves(self) -> None:
        """Recompute the forbidden points for the position on the board."""
        self.forbidden_moves = self.rule.forbidden_points(
            self.board, self.starting_color
        )

    def _is_stuck(self, color: Optional[Color]) -> bool:
        """``True`` when ``color`` is to move and every free point is forbidden."""
        return (
            color is not None
            and color is self.constrained_color
            and len(self.forbidden_moves) >= self.empty_count
        )

    @property
    def intersection_count(self) -> int:
        """Total number of playable intersections on this board."""
        return self.board_size * self.board_size

    @property
    def empty_count(self) -> int:
        """Number of intersections that are still free."""
        return self.intersection_count - self.move_count

    def is_board_full(self) -> bool:
        return self.move_count >= self.intersection_count

    def is_draw(self) -> bool:
        """``True`` when the board is full and nobody has won."""
        return self.winner is None and self.is_board_full()

    def board_snapshot(self) -> list[list[Optional[str]]]:
        """JSON friendly copy of the board (``board[y][x]``)."""
        return [[cell.value if cell else None for cell in row] for row in self.board]

    # ------------------------------------------------------------------
    # moves
    # ------------------------------------------------------------------
    def validate_move(self, color: Color, x: object, y: object) -> None:
        """Run the full validation chain without mutating any state.

        A finished game is checked first, so no stone can ever be added
        after a result has been decided, and the Renju restriction is
        checked last, so a forbidden point that is already occupied is
        reported as occupied rather than as forbidden.
        """
        if self.status is GameStatus.FINISHED:
            raise GameAlreadyFinishedError()
        if self.status is not GameStatus.PLAYING:
            raise GameNotStartedError()
        if color is not self.current_turn:
            current = self.current_turn.value if self.current_turn else "nobody"
            raise NotYourTurnError(f"It is the turn of {current}.")
        if not is_integer_coordinate(x) or not is_integer_coordinate(y):
            raise InvalidMoveError()
        x, y = cast(int, x), cast(int, y)  # narrowed by the integer check above
        if not in_bounds(self.board_size, x, y):
            raise OutOfRangeError(
                f"Coordinates must be within 0..{self.board_size - 1}."
            )
        if self.board[y][x] is not None:
            raise PositionOccupiedError()
        forbidden = self.forbidden_moves.get((x, y))
        if forbidden is not None and color is self.constrained_color:
            raise ForbiddenMoveError(forbidden, x, y)

    def make_move(self, color: Color, x: object, y: object) -> MoveResult:
        """Validate, apply and judge one move.

        Raises a :class:`app.errors.GameError` subclass when the move is
        rejected; the board is left untouched in that case.  A forbidden
        Renju point is such a rejection, so the game simply continues with
        the same player to move.
        """
        self.validate_move(color, x, y)
        x, y = cast(int, x), cast(int, y)

        self.board[y][x] = color
        self.move_count += 1
        self.last_move = (x, y)
        self.move_history.append(
            GomokuMove(x=x, y=y, color=color, sequence=self.move_count)
        )

        verdict = self.rule.evaluate_move(
            self.board, x, y, color, self.starting_color
        )
        if verdict.win:
            self._finish(
                winner=color,
                loser=color.opponent,
                reason=GameOverReason.FIVE_IN_A_ROW,
            )
        elif self.is_board_full():
            self._finish(winner=None, loser=None, reason=GameOverReason.DRAW)
        else:
            self.current_turn = color.opponent
            self._refresh_forbidden_moves()
            if self._is_stuck(self.current_turn):
                # The constrained player has to move but has nowhere legal to
                # go.  Blocking forbidden points removes the accidental loss,
                # not the Renju outcome of a genuinely dead position.
                self._finish(
                    winner=self.current_turn.opponent,
                    loser=self.current_turn,
                    reason=GameOverReason.NO_FORBIDDEN_FREE_MOVE,
                )

        return MoveResult(
            x=x,
            y=y,
            color=color,
            next_turn=self.current_turn,
            status=self.status,
            winner=self.winner,
            loser=self.loser,
            reason=self.reason,
        )

    def skip_turn(self, color: Color) -> Color:
        """Advance a timed-out Gomoku turn without changing board history."""
        if self.status is not GameStatus.PLAYING:
            raise GameNotStartedError()
        if color is not self.current_turn:
            raise NotYourTurnError()
        self.current_turn = color.opponent
        self._refresh_forbidden_moves()
        return self.current_turn

    def resign(self, color: Color) -> ResignResult:
        if self.status is not GameStatus.PLAYING:
            raise GameNotStartedError("Resignation requires a game in progress.")
        self._finish(color.opponent, color, GameOverReason.RESIGNATION)
        return ResignResult(GameType.GOMOKU, color.opponent, color)

    def undo_moves(self, count: int, next_turn: Color) -> UndoResult:
        """Remove the latest ``count`` accepted moves and restore a live game.

        Negotiation and the choice of one versus two moves belong to
        :class:`app.room.GameRoom`; this method owns only game invariants.
        """
        if self.status is not GameStatus.PLAYING:
            raise UndoNotAvailableError("Undo is available only while playing.")
        if count not in (1, 2) or count > len(self.move_history):
            raise UndoNotAvailableError()

        undone: list[GomokuMove] = []
        for _ in range(count):
            move = self.move_history.pop()
            self.board[move.y][move.x] = None
            undone.append(move)

        self.move_count -= count
        self.last_move = (
            (self.move_history[-1].x, self.move_history[-1].y)
            if self.move_history
            else None
        )
        self.current_turn = next_turn
        self.winner = None
        self.loser = None
        self.reason = None
        self.status = GameStatus.PLAYING
        self._refresh_forbidden_moves()
        return UndoResult(undone=tuple(undone), current_turn=next_turn)

    def check_win(self, x: int, y: int, color: Optional[Color] = None) -> bool:
        """``True`` when the stone at ``(x, y)`` is a winning stone."""
        if not in_bounds(self.board_size, x, y):
            return False
        stone = color or self.board[y][x]
        if stone is None:
            return False
        return self.rule.evaluate_move(
            self.board, x, y, stone, self.starting_color
        ).win
