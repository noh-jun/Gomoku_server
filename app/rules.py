"""Rule engines.

A rule engine answers two questions, and they are deliberately asked at
different moments:

* **Before** a stone is placed - may this player put a stone here at all?

  ```python
  kind = rule.classify_forbidden(board, x, y, color, starting_color)
  points = rule.forbidden_points(board, starting_color)
  ```

* **After** a stone has been placed - did that stone win the game?

  ```python
  verdict = rule.evaluate_move(board, x, y, color, starting_color)
  ```

Forbidden moves are *blocked*, never punished: a forbidden point is rejected
before it can reach the board, so a stone that is on the board is always
either an ordinary move or a winning one.

Two engines ship with the server:

* :class:`RenjuRule` (default) - the starting player (``starting_color``) is
  bound by the Renju forbidden moves (3-3, 4-4, overline) and needs exactly
  five in a row; the second player plays free style and wins with five *or
  more*.
* :class:`FreeStyleRule` - no forbidden moves for anybody, five or more wins.

Everything here is board-size agnostic: the dimensions are read from the
board that is passed in, so 15x15 and 19x19 share the same code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .board import DIRECTIONS, Board, Color, in_bounds
from .config import DEFAULT_WIN_LENGTH

#: Cell markers used by the line scanner.
_OWN = "o"
_EMPTY = "."
_BLOCK = "x"  # opponent stone or the edge of the board

#: Cells inspected on each side of the judged stone.  Six is enough to see a
#: five plus both of its ends, which is all any Renju pattern needs.
_WINDOW_RADIUS = 6

# ----------------------------------------------------------------------
# Board scan prefilter: a pure speed-up, never a rule
# ----------------------------------------------------------------------
#: :meth:`RenjuRule.forbidden_points` probes a point only when enough own
#: stones sit close enough to make *any* forbidden pattern possible.  The
#: cheapest forbidden move is a 3-3, and it needs two axes carrying two other
#: own stones each; the four axes through a point share nothing but the point
#: itself, so those four stones can never be double counted.
_PREFILTER_MIN_STONES = 4

#: Radius searched per axis, as ``win_length - _PREFILTER_RADIUS_OFFSET``.
#: An overline through the point reaches at most ``win_length`` cells away,
#: which still leaves at least four of its stones inside this radius.
_PREFILTER_RADIUS_OFFSET = 1

#: Below this win length the bound above no longer holds, so the prefilter
#: switches itself off and every empty point is probed.
_PREFILTER_MIN_WIN_LENGTH = 5


class ForbiddenType(str, Enum):
    """Why a move of the constrained player was illegal."""

    DOUBLE_THREE = "DOUBLE_THREE"
    DOUBLE_FOUR = "DOUBLE_FOUR"
    OVERLINE = "OVERLINE"


@dataclass(frozen=True)
class RuleVerdict:
    """Result of judging one placed stone.

    There is no forbidden case here: a forbidden point never becomes a
    placed stone (see :meth:`Rule.classify_forbidden`).
    """

    win: bool = False


class Rule(ABC):
    """Strategy describing one Gomoku/Renju variant."""

    #: Human readable name, exposed through the HTTP index and the GUI.
    name: str = "rule"

    def __init__(self, win_length: int = DEFAULT_WIN_LENGTH) -> None:
        #: Stones in a row needed to win under this rule.
        self.win_length: int = win_length

    @abstractmethod
    def evaluate_move(
        self,
        board: Board,
        x: int,
        y: int,
        color: Color,
        starting_color: Color,
    ) -> RuleVerdict:
        """Judge the stone of ``color`` that now sits at ``(x, y)``.

        The stone must already be on ``board``; the engine never mutates the
        board permanently (temporary probes are always undone).
        """

    # ------------------------------------------------------------------
    # forbidden moves (asked *before* the stone is placed)
    # ------------------------------------------------------------------
    def constrained_color(self, starting_color: Color) -> Optional[Color]:
        """The color bound by forbidden moves, ``None`` when nobody is."""
        return None

    def classify_forbidden(
        self,
        board: Board,
        x: int,
        y: int,
        color: Color,
        starting_color: Color,
    ) -> Optional[ForbiddenType]:
        """Why ``color`` may not play ``(x, y)``, or ``None`` when it may.

        ``(x, y)`` has to be an empty point.  The board is always left
        exactly as it was found.
        """
        return None

    def forbidden_points(
        self, board: Board, starting_color: Color
    ) -> dict[tuple[int, int], ForbiddenType]:
        """Every empty point the constrained color may not play right now."""
        return {}

    # ------------------------------------------------------------------
    # shared line helpers
    # ------------------------------------------------------------------
    def _window(
        self, board: Board, x: int, y: int, dx: int, dy: int, color: Color
    ) -> str:
        """Return the line through ``(x, y)`` as ``o`` / ``.`` / ``x`` chars.

        The judged stone always sits at the centre index.
        """
        board_size = len(board)
        chars: list[str] = []
        for step in range(-_WINDOW_RADIUS, _WINDOW_RADIUS + 1):
            cx, cy = x + dx * step, y + dy * step
            if not in_bounds(board_size, cx, cy):
                chars.append(_BLOCK)
                continue
            cell = board[cy][cx]
            if cell is None:
                chars.append(_EMPTY)
            elif cell is color:
                chars.append(_OWN)
            else:
                chars.append(_BLOCK)
        return "".join(chars)

    def longest_run(self, board: Board, x: int, y: int, color: Color) -> int:
        """Longest chain of ``color`` through ``(x, y)`` over all axes."""
        return max(
            _run_length(self._window(board, x, y, dx, dy, color), _CENTRE)
            for dx, dy in DIRECTIONS
        )


class FreeStyleRule(Rule):
    """Free-style (자유룰): no forbidden moves, five or more in a row wins."""

    name = "free_style"

    def evaluate_move(
        self,
        board: Board,
        x: int,
        y: int,
        color: Color,
        starting_color: Color,
    ) -> RuleVerdict:
        if self.longest_run(board, x, y, color) >= self.win_length:
            return RuleVerdict(win=True)
        return RuleVerdict()


class RenjuRule(Rule):
    """Renju rules applied to the starting player.

    The forbidden moves are bound to *the player who moves first* rather
    than to a fixed color, so the same engine works for the WHITE-first
    setup of this server and for a classic BLACK-first game.

    Forbidden points are blocked instead of punished, so the constrained
    player can never end up with an overline on the board.

    Judging order (see the spec):
    overline -> exact five -> double four -> double three -> plain move.
    """

    name = "renju"

    #: Nesting limit for the recursive "can this three become a four?" test.
    MAX_JUDGE_DEPTH = 3

    def constrained_color(self, starting_color: Color) -> Optional[Color]:
        return starting_color

    def evaluate_move(
        self,
        board: Board,
        x: int,
        y: int,
        color: Color,
        starting_color: Color,
    ) -> RuleVerdict:
        if color is not starting_color:
            # The second player is unconstrained: five or more wins.
            return RuleVerdict(
                win=self.longest_run(board, x, y, color) >= self.win_length
            )
        # Overlines are blocked before they can be played, so the run of the
        # constrained player can never grow past ``win_length``.
        return RuleVerdict(win=self.check_exact_five(board, x, y, color))

    def classify_forbidden(
        self,
        board: Board,
        x: int,
        y: int,
        color: Color,
        starting_color: Color,
    ) -> Optional[ForbiddenType]:
        if color is not starting_color:
            return None
        if not in_bounds(len(board), x, y) or board[y][x] is not None:
            return None
        board[y][x] = color  # temporary probe, always undone below
        try:
            return self._classify_placed(board, x, y, color)
        finally:
            board[y][x] = None

    def forbidden_points(
        self, board: Board, starting_color: Color
    ) -> dict[tuple[int, int], ForbiddenType]:
        color = starting_color
        board_size = len(board)
        points: dict[tuple[int, int], ForbiddenType] = {}
        for y in range(board_size):
            for x in range(board_size):
                if board[y][x] is not None:
                    continue
                if not self._may_be_forbidden(board, x, y, color):
                    continue
                board[y][x] = color  # temporary probe, always undone below
                try:
                    kind = self._classify_placed(board, x, y, color)
                finally:
                    board[y][x] = None
                if kind is not None:
                    points[(x, y)] = kind
        return points

    def _may_be_forbidden(
        self, board: Board, x: int, y: int, color: Color
    ) -> bool:
        """Cheap necessary condition for ``(x, y)`` being forbidden.

        Skipping a point that fails this test can never hide a forbidden
        move; the bound is documented at ``_PREFILTER_MIN_STONES``.
        """
        if self.win_length < _PREFILTER_MIN_WIN_LENGTH:
            return True
        board_size = len(board)
        radius = self.win_length - _PREFILTER_RADIUS_OFFSET
        found = 0
        for dx, dy in DIRECTIONS:
            for step in range(-radius, radius + 1):
                if step == 0:
                    continue
                cx, cy = x + dx * step, y + dy * step
                if in_bounds(board_size, cx, cy) and board[cy][cx] is color:
                    found += 1
                    if found >= _PREFILTER_MIN_STONES:
                        return True
        return False

    # ------------------------------------------------------------------
    # individual rule checks (all expect the stone to be on the board)
    # ------------------------------------------------------------------
    def check_overline(self, board: Board, x: int, y: int, color: Color) -> bool:
        """More than ``win_length`` stones in a row (장목)."""
        return self.longest_run(board, x, y, color) > self.win_length

    def check_exact_five(self, board: Board, x: int, y: int, color: Color) -> bool:
        """Exactly ``win_length`` stones in a row on at least one axis."""
        for dx, dy in DIRECTIONS:
            if _run_length(self._window(board, x, y, dx, dy, color), _CENTRE) == self.win_length:
                return True
        return False

    def count_fours(self, board: Board, x: int, y: int, color: Color) -> int:
        """Number of axes on which this stone created a four."""
        return sum(
            1
            for dx, dy in DIRECTIONS
            if self._is_four(self._window(board, x, y, dx, dy, color))
        )

    def check_double_four(self, board: Board, x: int, y: int, color: Color) -> bool:
        """Two or more independent fours created by one move (4-4)."""
        return self.count_fours(board, x, y, color) >= 2

    def count_open_threes(
        self, board: Board, x: int, y: int, color: Color, depth: int = 0
    ) -> int:
        """Number of axes on which this stone created an open (live) three."""
        return sum(
            1
            for dx, dy in DIRECTIONS
            if self._is_open_three(board, x, y, color, dx, dy, depth)
        )

    def check_double_three(
        self, board: Board, x: int, y: int, color: Color, depth: int = 0
    ) -> bool:
        """Two or more independent open threes created by one move (3-3)."""
        return self.count_open_threes(board, x, y, color, depth) >= 2

    def _classify_placed(
        self, board: Board, x: int, y: int, color: Color, depth: int = 0
    ) -> Optional[ForbiddenType]:
        """Which forbidden move the stone at ``(x, y)`` is, if any.

        The stone has to be on the board already; callers that judge a point
        before playing it place a temporary probe first.
        """
        if self.check_overline(board, x, y, color):
            return ForbiddenType.OVERLINE
        if self.check_exact_five(board, x, y, color):
            # A move that makes five is never punished as 4-4 or 3-3.
            return None
        if self.check_double_four(board, x, y, color):
            return ForbiddenType.DOUBLE_FOUR
        if self.check_double_three(board, x, y, color, depth):
            return ForbiddenType.DOUBLE_THREE
        return None

    def is_forbidden(
        self, board: Board, x: int, y: int, color: Color, depth: int = 0
    ) -> bool:
        """``True`` when the stone at ``(x, y)`` is a forbidden move.

        Kept as the recursion entry point of
        :meth:`_completion_is_forbidden`, which needs only the yes/no answer.
        """
        return self._classify_placed(board, x, y, color, depth) is not None

    # ------------------------------------------------------------------
    # pattern helpers
    # ------------------------------------------------------------------
    def _is_four(self, window: str) -> bool:
        """``True`` when one more stone on this line completes an exact five.

        The five has to contain the judged stone, which is what makes the
        four *created by this move*.
        """
        for index, cell in enumerate(window):
            if cell != _EMPTY:
                continue
            candidate = _with_stone(window, index)
            if _run_length(candidate, _CENTRE) == self.win_length:
                return True
        return False

    def _is_open_three(
        self,
        board: Board,
        x: int,
        y: int,
        color: Color,
        dx: int,
        dy: int,
        depth: int,
    ) -> bool:
        """``True`` when this stone created a three that can become an open four.

        That is the working definition of a live three: there is an empty
        point on the same line where the player could legally play and end
        up with a straight (double ended) four containing this stone.
        """
        window = self._window(board, x, y, dx, dy, color)
        for index, cell in enumerate(window):
            if cell != _EMPTY:
                continue
            candidate = _with_stone(window, index)
            if not _is_straight_four(candidate, index, self.win_length):
                continue
            start, end = _run_bounds(candidate, index)
            if not start <= _CENTRE <= end:
                # The four must contain the stone that was just played.
                continue
            if self._completion_is_forbidden(board, x, y, dx, dy, index, color, depth):
                continue
            return True
        return False

    def _completion_is_forbidden(
        self,
        board: Board,
        x: int,
        y: int,
        dx: int,
        dy: int,
        index: int,
        color: Color,
        depth: int,
    ) -> bool:
        """Would the stone that turns the three into a four be illegal itself?

        Renju judges this recursively; the depth guard keeps pathological
        boards from blowing the stack, and an undecided case counts as
        legal (the three stays live).
        """
        if depth >= self.MAX_JUDGE_DEPTH:
            return False

        step = index - _CENTRE
        px, py = x + dx * step, y + dy * step
        if not in_bounds(len(board), px, py) or board[py][px] is not None:
            return False

        board[py][px] = color  # temporary probe, always undone below
        try:
            return self.is_forbidden(board, px, py, color, depth + 1)
        finally:
            board[py][px] = None


# ----------------------------------------------------------------------
# window level helpers (module private, pure string maths)
# ----------------------------------------------------------------------
#: Index of the judged stone inside a window produced by ``Rule._window``.
_CENTRE = _WINDOW_RADIUS


def _with_stone(window: str, index: int) -> str:
    return window[:index] + _OWN + window[index + 1 :]


def _run_bounds(window: str, index: int) -> tuple[int, int]:
    """Inclusive bounds of the own-stone chain that contains ``index``."""
    start = index
    while start - 1 >= 0 and window[start - 1] == _OWN:
        start -= 1
    end = index
    while end + 1 < len(window) and window[end + 1] == _OWN:
        end += 1
    return start, end


def _run_length(window: str, index: int) -> int:
    if window[index] != _OWN:
        return 0
    start, end = _run_bounds(window, index)
    return end - start + 1


def _is_straight_four(window: str, index: int, win_length: int) -> bool:
    """``True`` when the chain through ``index`` is a four open on both ends."""
    start, end = _run_bounds(window, index)
    if end - start + 1 != win_length - 1:
        return False
    if start - 1 < 0 or end + 1 >= len(window):
        return False
    return window[start - 1] == _EMPTY and window[end + 1] == _EMPTY
