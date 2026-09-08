"""Board primitives for the Gomoku domain.

This module is intentionally dependency free: it only knows about stone
colors, coordinates and the raw board container.  Everything above it
(rules, game, room, network) builds on these types.

There is deliberately no board size constant here - the size is chosen by
the server operator (see :mod:`app.config`) and carried by each game.
"""

from __future__ import annotations

from enum import Enum
from typing import Final, Optional

#: Half-directions used for win detection.  Each one is checked forwards
#: *and* backwards, which covers all eight directions.
DIRECTIONS: Final[tuple[tuple[int, int], ...]] = (
    (1, 0),   # horizontal  -
    (0, 1),   # vertical    |
    (1, 1),   # diagonal    \
    (1, -1),  # diagonal    /
)


class Color(str, Enum):
    """Stone / player color.  ``BLACK`` always moves first."""

    BLACK = "BLACK"
    WHITE = "WHITE"

    @property
    def opponent(self) -> "Color":
        return Color.WHITE if self is Color.BLACK else Color.BLACK


#: ``board[y][x]`` holds the stone at that intersection, ``None`` when empty.
Board = list[list[Optional[Color]]]


def create_board(board_size: int) -> Board:
    """Return an empty ``board_size`` x ``board_size`` board."""
    return [[None for _ in range(board_size)] for _ in range(board_size)]


def in_bounds(board_size: int, x: int, y: int) -> bool:
    """Return ``True`` when ``(x, y)`` is on a ``board_size`` square board."""
    return 0 <= x < board_size and 0 <= y < board_size


def is_integer_coordinate(value: object) -> bool:
    """Return ``True`` only for real integers.

    ``bool`` is a subclass of ``int`` in Python, and neither ``True`` nor
    ``1.0`` may be accepted as a coordinate coming from an untrusted client.
    """
    return isinstance(value, int) and not isinstance(value, bool)
