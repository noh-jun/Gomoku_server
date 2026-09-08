"""Pure, authoritative Othello game engine."""

from __future__ import annotations

from typing import Optional, cast

from .board import Board, Color, create_board, in_bounds, is_integer_coordinate
from .config import GameSettings, OTHELLO_BOARD_SIZE, OTHELLO_STARTING_COLOR
from .errors import (
    GameAlreadyFinishedError,
    GameNotStartedError,
    InvalidMoveError,
    NotYourTurnError,
    OutOfRangeError,
    PositionOccupiedError,
)
from .game import ChangedCell, GameOverReason, GameStatus, MoveResult, ResignResult
from .game_type import GameType

_DIRECTIONS: tuple[tuple[int, int], ...] = tuple(
    (dx, dy)
    for dy in (-1, 0, 1)
    for dx in (-1, 0, 1)
    if (dx, dy) != (0, 0)
)


class OthelloGame:
    """Standard 8x8 Othello with automatic pass handling."""

    def __init__(self, status: GameStatus = GameStatus.PLAYING) -> None:
        self.board_size = OTHELLO_BOARD_SIZE
        self.win_length: None = None
        self.starting_color = OTHELLO_STARTING_COLOR
        self.board: Board = create_board(self.board_size)
        self.current_turn: Optional[Color] = self.starting_color
        self.winner: Optional[Color] = None
        self.loser: Optional[Color] = None
        self.status = status
        self.reason: Optional[GameOverReason] = None
        self.move_count = 0
        self.last_move: Optional[tuple[int, int]] = None
        self._place_initial_stones()

    @classmethod
    def from_settings(
        cls, settings: GameSettings, status: GameStatus = GameStatus.PLAYING
    ) -> "OthelloGame":
        if settings.game_type is not GameType.OTHELLO:
            raise ValueError("OthelloGame requires OTHELLO settings.")
        return cls(status=status)

    @property
    def game_type(self) -> GameType:
        return GameType.OTHELLO

    @property
    def settings(self) -> GameSettings:
        return GameSettings(
            game_type=GameType.OTHELLO,
            board_size=self.board_size,
            win_length=None,
            starting_color=self.starting_color,
        )

    @property
    def rule_name(self) -> str:
        return "othello"

    def _place_initial_stones(self) -> None:
        middle = self.board_size // 2
        self.board[middle - 1][middle - 1] = Color.WHITE
        self.board[middle][middle] = Color.WHITE
        self.board[middle - 1][middle] = Color.BLACK
        self.board[middle][middle - 1] = Color.BLACK
        self.move_count = 4

    def reset(
        self,
        starting_color: Optional[Color] = None,
        status: GameStatus = GameStatus.PLAYING,
    ) -> None:
        if (
            starting_color is not None
            and starting_color is not OTHELLO_STARTING_COLOR
        ):
            raise ValueError("Othello always starts with BLACK.")
        self.board = create_board(self.board_size)
        self.current_turn = self.starting_color
        self.winner = None
        self.loser = None
        self.status = status
        self.reason = None
        self.last_move = None
        self._place_initial_stones()

    def start(self) -> None:
        self.status = GameStatus.PLAYING
        if self.current_turn is None:
            self.current_turn = self.starting_color

    def suspend(self) -> None:
        if self.status is GameStatus.PLAYING:
            self.status = GameStatus.WAITING

    def board_snapshot(self) -> list[list[Optional[str]]]:
        return [[cell.value if cell else None for cell in row] for row in self.board]

    def stone_at(self, x: int, y: int) -> Optional[Color]:
        return self.board[y][x]

    def _flips_for_move(self, color: Color, x: int, y: int) -> list[tuple[int, int]]:
        if not in_bounds(self.board_size, x, y) or self.board[y][x] is not None:
            return []
        flips: list[tuple[int, int]] = []
        for dx, dy in _DIRECTIONS:
            line: list[tuple[int, int]] = []
            cx, cy = x + dx, y + dy
            while in_bounds(self.board_size, cx, cy) and self.board[cy][cx] is color.opponent:
                line.append((cx, cy))
                cx, cy = cx + dx, cy + dy
            if line and in_bounds(self.board_size, cx, cy) and self.board[cy][cx] is color:
                flips.extend(line)
        return flips

    def legal_moves(self, color: Color) -> list[tuple[int, int]]:
        return [
            (x, y)
            for y in range(self.board_size)
            for x in range(self.board_size)
            if self._flips_for_move(color, x, y)
        ]

    @property
    def legal_moves_for_current_turn(self) -> list[tuple[int, int]]:
        if self.status is not GameStatus.PLAYING or self.current_turn is None:
            return []
        return self.legal_moves(self.current_turn)

    @property
    def score(self) -> dict[Color, int]:
        return {
            color: sum(cell is color for row in self.board for cell in row)
            for color in Color
        }

    def validate_move(self, color: Color, x: object, y: object) -> None:
        if self.status is GameStatus.FINISHED:
            raise GameAlreadyFinishedError()
        if self.status is not GameStatus.PLAYING:
            raise GameNotStartedError()
        if color is not self.current_turn:
            raise NotYourTurnError(f"It is the turn of {self.current_turn.value}.")
        if not is_integer_coordinate(x) or not is_integer_coordinate(y):
            raise InvalidMoveError()
        x, y = cast(int, x), cast(int, y)
        if not in_bounds(self.board_size, x, y):
            raise OutOfRangeError("Coordinates must be within 0..7.")
        if self.board[y][x] is not None:
            raise PositionOccupiedError()
        if not self._flips_for_move(color, x, y):
            raise InvalidMoveError("An Othello move must flip at least one stone.")

    def _finish(self, reason: GameOverReason) -> None:
        score = self.score
        if score[Color.BLACK] == score[Color.WHITE]:
            self.winner = self.loser = None
        else:
            self.winner = max(score, key=score.__getitem__)
            self.loser = self.winner.opponent
        self.reason = reason
        self.status = GameStatus.FINISHED
        self.current_turn = None

    def make_move(self, color: Color, x: object, y: object) -> MoveResult:
        self.validate_move(color, x, y)
        x, y = cast(int, x), cast(int, y)
        flips = self._flips_for_move(color, x, y)
        self.board[y][x] = color
        for fx, fy in flips:
            self.board[fy][fx] = color
        self.move_count += 1
        self.last_move = (x, y)

        passed_color: Optional[Color] = None
        if self.move_count == self.board_size * self.board_size:
            self._finish(GameOverReason.BOARD_FULL)
        elif self.legal_moves(color.opponent):
            self.current_turn = color.opponent
        elif self.legal_moves(color):
            passed_color = color.opponent
            self.current_turn = color
        else:
            self._finish(GameOverReason.NO_LEGAL_MOVES)

        return MoveResult(
            x=x,
            y=y,
            color=color,
            next_turn=self.current_turn,
            status=self.status,
            winner=self.winner,
            loser=self.loser,
            reason=self.reason,
            flipped=tuple(ChangedCell(fx, fy, color) for fx, fy in flips),
            passed_color=passed_color,
            score=self.score,
            game_type=GameType.OTHELLO,
        )

    def resign(self, color: Color) -> ResignResult:
        if self.status is not GameStatus.PLAYING:
            raise GameNotStartedError("Resignation requires a game in progress.")
        self.winner = color.opponent
        self.loser = color
        self.reason = GameOverReason.RESIGNATION
        self.status = GameStatus.FINISHED
        self.current_turn = None
        return ResignResult(
            GameType.OTHELLO,
            color.opponent,
            color,
            score=self.score,
        )
