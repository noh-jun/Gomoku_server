"""Unit tests for the rule engines (Renju forbidden moves and free style).

Two different questions are tested here, and they are asked at different
moments:

* ``evaluate_move`` judges a stone that is **already on the board** and only
  answers "did this win?".
* ``classify_forbidden`` / ``forbidden_points`` judge an **empty point**
  before anything is played, which is how the server blocks forbidden moves.

The position fixtures below include the judged stone, so
:func:`classify` lifts it off a copy of the board before asking.
"""

from __future__ import annotations

import random
from typing import Iterable, Optional

import pytest

from app.board import Board, Color, create_board
from app.config import DEFAULT_WIN_LENGTH
from app.rules import ForbiddenType, FreeStyleRule, RenjuRule, RuleVerdict

Coords = Iterable[tuple[int, int]]

#: WHITE opens in this server, so WHITE is the constrained player.
FIRST = Color.WHITE
SECOND = Color.BLACK


@pytest.fixture
def renju() -> RenjuRule:
    return RenjuRule(DEFAULT_WIN_LENGTH)


def build_board(
    size: int = 15, white: Coords = (), black: Coords = ()
) -> Board:
    board = create_board(size)
    for x, y in white:
        board[y][x] = Color.WHITE
    for x, y in black:
        board[y][x] = Color.BLACK
    return board


def judge(
    rule: RenjuRule,
    board: Board,
    x: int,
    y: int,
    color: Color = FIRST,
    starting_color: Color = FIRST,
) -> RuleVerdict:
    """Judge the stone that already sits at ``(x, y)``."""
    return rule.evaluate_move(board, x, y, color, starting_color)


def classify(
    rule: RenjuRule,
    board: Board,
    x: int,
    y: int,
    color: Color = FIRST,
    starting_color: Color = FIRST,
) -> Optional[ForbiddenType]:
    """Judge ``(x, y)`` the way the server does: before the stone is played.

    Works on a copy with the point cleared, so the caller's board keeps the
    stone and stays usable for ``count_fours`` / ``count_open_threes``.
    """
    probe = [row.copy() for row in board]
    probe[y][x] = None
    return rule.classify_forbidden(probe, x, y, color, starting_color)


def occupied(board: Board) -> set[tuple[int, int]]:
    return {
        (x, y)
        for y, row in enumerate(board)
        for x, cell in enumerate(row)
        if cell is not None
    }


def unfiltered_points(
    rule: RenjuRule, board: Board, starting_color: Color = FIRST
) -> dict[tuple[int, int], ForbiddenType]:
    """``forbidden_points`` with the speed-up prefilter disabled."""
    rule._may_be_forbidden = lambda *args, **kwargs: True  # type: ignore[method-assign]
    try:
        return rule.forbidden_points(board, starting_color)
    finally:
        del rule._may_be_forbidden  # type: ignore[attr-defined]


def scattered_board(
    seed: int, size: int, stones: int
) -> Board:
    """A plausible middle game: stones clustered around the centre."""
    rng = random.Random(seed)
    cells = [(x, y) for y in range(size) for x in range(size)]
    cells.sort(
        key=lambda c: abs(c[0] - size // 2) + abs(c[1] - size // 2) + rng.random() * 6
    )
    board = create_board(size)
    for index, (x, y) in enumerate(cells[:stones]):
        board[y][x] = FIRST if index % 2 == 0 else SECOND
    return board


# ----------------------------------------------------------------------
# winning shapes
# ----------------------------------------------------------------------
def test_exact_five_wins(renju: RenjuRule) -> None:
    stones = [(3, 7), (4, 7), (5, 7), (6, 7), (7, 7)]
    board = build_board(white=stones)

    assert judge(renju, board, 7, 7).win is True
    assert classify(renju, board, 7, 7) is None
    assert renju.check_exact_five(board, 7, 7, FIRST) is True
    assert renju.check_overline(board, 7, 7, FIRST) is False


@pytest.mark.parametrize(
    "stones",
    [
        [(7, 3), (7, 4), (7, 5), (7, 6), (7, 7)],  # vertical
        [(3, 3), (4, 4), (5, 5), (6, 6), (7, 7)],  # backslash
        [(11, 3), (10, 4), (9, 5), (8, 6), (7, 7)],  # slash
    ],
)
def test_five_in_every_direction(
    renju: RenjuRule, stones: list[tuple[int, int]]
) -> None:
    board = build_board(white=stones)

    assert judge(renju, board, 7, 7).win is True


def test_five_completed_from_the_middle(renju: RenjuRule) -> None:
    board = build_board(white=[(5, 7), (6, 7), (7, 7), (8, 7), (9, 7)])

    assert judge(renju, board, 7, 7).win is True


# ----------------------------------------------------------------------
# overline (장목)
# ----------------------------------------------------------------------
def test_overline_is_forbidden_for_the_first_player(renju: RenjuRule) -> None:
    board = build_board(white=[(3, 7), (4, 7), (5, 7), (6, 7), (7, 7), (8, 7)])

    assert classify(renju, board, 7, 7) is ForbiddenType.OVERLINE


def test_long_overline_is_forbidden(renju: RenjuRule) -> None:
    board = build_board(
        white=[(2, 7), (3, 7), (4, 7), (5, 7), (6, 7), (7, 7), (8, 7)]
    )

    assert classify(renju, board, 7, 7) is ForbiddenType.OVERLINE


def test_overline_is_judged_before_a_four(renju: RenjuRule) -> None:
    board = build_board(
        white=[
            (2, 7), (3, 7), (4, 7), (5, 7), (6, 7), (7, 7),  # six in a row
            (7, 4), (7, 5), (7, 6),  # plus a vertical four
        ]
    )

    assert classify(renju, board, 7, 7) is ForbiddenType.OVERLINE


def test_five_is_judged_before_a_double_four(renju: RenjuRule) -> None:
    """A move that makes five is allowed and wins, 4-4 shape or not."""
    board = build_board(
        white=[
            (3, 7), (4, 7), (5, 7), (6, 7), (7, 7),  # exactly five
            (7, 4), (7, 5), (7, 6),  # and a vertical four
        ]
    )

    assert classify(renju, board, 7, 7) is None
    assert judge(renju, board, 7, 7).win is True


# ----------------------------------------------------------------------
# double four (4-4)
# ----------------------------------------------------------------------
def test_double_four_is_forbidden(renju: RenjuRule) -> None:
    board = build_board(
        white=[(4, 7), (5, 7), (6, 7), (7, 7), (7, 4), (7, 5), (7, 6)]
    )

    assert classify(renju, board, 7, 7) is ForbiddenType.DOUBLE_FOUR
    assert renju.count_fours(board, 7, 7, FIRST) == 2


def test_a_single_four_is_allowed(renju: RenjuRule) -> None:
    board = build_board(white=[(4, 7), (5, 7), (6, 7), (7, 7)])

    assert classify(renju, board, 7, 7) is None
    assert judge(renju, board, 7, 7).win is False
    assert renju.count_fours(board, 7, 7, FIRST) == 1


def test_four_plus_three_is_allowed(renju: RenjuRule) -> None:
    board = build_board(
        white=[(4, 7), (5, 7), (6, 7), (7, 7), (7, 5), (7, 6)]
    )

    assert classify(renju, board, 7, 7) is None
    assert renju.count_fours(board, 7, 7, FIRST) == 1
    assert renju.count_open_threes(board, 7, 7, FIRST) == 1


# ----------------------------------------------------------------------
# double three (3-3)
# ----------------------------------------------------------------------
def test_double_three_is_forbidden(renju: RenjuRule) -> None:
    board = build_board(white=[(5, 7), (6, 7), (7, 7), (7, 5), (7, 6)])

    assert classify(renju, board, 7, 7) is ForbiddenType.DOUBLE_THREE
    assert renju.count_open_threes(board, 7, 7, FIRST) == 2
    assert renju.count_fours(board, 7, 7, FIRST) == 0


def test_double_three_with_a_broken_three(renju: RenjuRule) -> None:
    """``o . o o`` is a live three as well, so this is also a 3-3."""
    board = build_board(white=[(5, 7), (7, 7), (8, 7), (7, 5), (7, 6)])

    assert classify(renju, board, 7, 7) is ForbiddenType.DOUBLE_THREE


def test_a_single_open_three_is_allowed(renju: RenjuRule) -> None:
    board = build_board(white=[(5, 7), (6, 7), (7, 7)])

    assert classify(renju, board, 7, 7) is None
    assert renju.count_open_threes(board, 7, 7, FIRST) == 1


def test_a_blocked_three_is_not_a_live_three(renju: RenjuRule) -> None:
    board = build_board(white=[(5, 7), (6, 7), (7, 7)], black=[(4, 7), (8, 7)])

    assert renju.count_open_threes(board, 7, 7, FIRST) == 0


def test_blocked_three_plus_open_three_is_allowed(renju: RenjuRule) -> None:
    """Only one of the two threes is live, so the point stays playable."""
    board = build_board(
        white=[(5, 7), (6, 7), (7, 7), (7, 5), (7, 6)],
        black=[(4, 7)],
    )

    assert classify(renju, board, 7, 7) is None
    assert renju.count_open_threes(board, 7, 7, FIRST) == 1


def test_three_at_the_board_edge_is_not_live(renju: RenjuRule) -> None:
    """The wall blocks a three just like an opponent stone does."""
    board = build_board(white=[(0, 7), (1, 7), (2, 7)])

    assert renju.count_open_threes(board, 2, 7, FIRST) == 0


def test_is_forbidden_helper(renju: RenjuRule) -> None:
    """The bool wrapper used by the recursive three-to-four test."""
    forbidden = build_board(white=[(5, 7), (6, 7), (7, 7), (7, 5), (7, 6)])
    allowed = build_board(white=[(5, 7), (6, 7), (7, 7)])

    assert renju.is_forbidden(forbidden, 7, 7, FIRST) is True
    assert renju.is_forbidden(allowed, 7, 7, FIRST) is False


# ----------------------------------------------------------------------
# probes never survive
# ----------------------------------------------------------------------
def test_evaluate_move_does_not_leave_probe_stones_behind(
    renju: RenjuRule,
) -> None:
    stones = [(5, 7), (6, 7), (7, 7), (7, 5), (7, 6)]
    board = build_board(white=stones)

    judge(renju, board, 7, 7)

    assert occupied(board) == set(stones)


def test_classify_forbidden_leaves_the_board_untouched(renju: RenjuRule) -> None:
    stones = [(5, 7), (6, 7), (7, 5), (7, 6)]
    board = build_board(white=stones)
    before = [row.copy() for row in board]

    kind = renju.classify_forbidden(board, 7, 7, FIRST, FIRST)

    assert kind is ForbiddenType.DOUBLE_THREE
    assert board == before


def test_forbidden_points_leaves_the_board_untouched(renju: RenjuRule) -> None:
    board = scattered_board(seed=11, size=15, stones=40)
    before = [row.copy() for row in board]

    renju.forbidden_points(board, FIRST)

    assert board == before


def test_classify_forbidden_ignores_an_occupied_point(renju: RenjuRule) -> None:
    """Occupancy is the caller's business; the rule engine only judges shape."""
    board = build_board(white=[(5, 7), (6, 7), (7, 5), (7, 6)], black=[(7, 7)])

    assert renju.classify_forbidden(board, 7, 7, FIRST, FIRST) is None


def test_classify_forbidden_ignores_a_point_off_the_board(
    renju: RenjuRule,
) -> None:
    board = build_board()

    assert renju.classify_forbidden(board, -1, 7, FIRST, FIRST) is None
    assert renju.classify_forbidden(board, 15, 7, FIRST, FIRST) is None


# ----------------------------------------------------------------------
# the whole-board scan
# ----------------------------------------------------------------------
def test_forbidden_points_finds_the_double_three(renju: RenjuRule) -> None:
    board = build_board(white=[(5, 7), (6, 7), (7, 5), (7, 6)])

    points = renju.forbidden_points(board, FIRST)

    assert points[(7, 7)] is ForbiddenType.DOUBLE_THREE


def test_forbidden_points_never_includes_an_occupied_point(
    renju: RenjuRule,
) -> None:
    board = scattered_board(seed=3, size=15, stones=60)

    points = renju.forbidden_points(board, FIRST)

    assert not (points.keys() & occupied(board))


def test_forbidden_points_is_empty_on_an_empty_board(renju: RenjuRule) -> None:
    assert renju.forbidden_points(create_board(15), FIRST) == {}


def test_forbidden_points_follows_the_starting_color(renju: RenjuRule) -> None:
    """The same shape in BLACK is only forbidden when BLACK opens."""
    board = build_board(black=[(5, 7), (6, 7), (7, 5), (7, 6)])

    assert renju.forbidden_points(board, FIRST) == {}
    assert renju.forbidden_points(board, SECOND)[(7, 7)] is (
        ForbiddenType.DOUBLE_THREE
    )


@pytest.mark.parametrize("size,stones", [(15, 20), (15, 60), (19, 40), (19, 120)])
@pytest.mark.parametrize("seed", range(5))
def test_the_prefilter_never_changes_the_answer(
    renju: RenjuRule, size: int, stones: int, seed: int
) -> None:
    """The scan speed-up must be invisible: same points, same kinds."""
    board = scattered_board(seed=seed, size=size, stones=stones)

    assert renju.forbidden_points(board, FIRST) == unfiltered_points(
        renju, board, FIRST
    )


def test_forbidden_points_agrees_with_classify_forbidden(
    renju: RenjuRule,
) -> None:
    """The scan is exactly the per-point judgement, applied everywhere."""
    board = scattered_board(seed=7, size=15, stones=50)

    one_by_one: dict[tuple[int, int], ForbiddenType] = {}
    for y in range(15):
        for x in range(15):
            if board[y][x] is not None:
                continue
            kind = renju.classify_forbidden(board, x, y, FIRST, FIRST)
            if kind is not None:
                one_by_one[(x, y)] = kind

    assert renju.forbidden_points(board, FIRST) == one_by_one


# ----------------------------------------------------------------------
# the second player is unconstrained
# ----------------------------------------------------------------------
def test_second_player_may_play_a_double_three(renju: RenjuRule) -> None:
    board = build_board(black=[(5, 7), (6, 7), (7, 7), (7, 5), (7, 6)])

    assert classify(renju, board, 7, 7, color=SECOND) is None
    assert judge(renju, board, 7, 7, color=SECOND).win is False


def test_second_player_may_play_a_double_four(renju: RenjuRule) -> None:
    board = build_board(
        black=[(4, 7), (5, 7), (6, 7), (7, 7), (7, 4), (7, 5), (7, 6)]
    )

    assert classify(renju, board, 7, 7, color=SECOND) is None


def test_second_player_wins_with_six(renju: RenjuRule) -> None:
    board = build_board(black=[(3, 7), (4, 7), (5, 7), (6, 7), (7, 7), (8, 7)])

    assert classify(renju, board, 7, 7, color=SECOND) is None
    assert judge(renju, board, 7, 7, color=SECOND).win is True


def test_forbidden_moves_follow_the_starting_color(renju: RenjuRule) -> None:
    """With BLACK opening, BLACK is the one bound by the 3-3 rule."""
    board = build_board(black=[(5, 7), (6, 7), (7, 7), (7, 5), (7, 6)])

    as_second = classify(renju, board, 7, 7, color=SECOND, starting_color=FIRST)
    as_first = classify(renju, board, 7, 7, color=SECOND, starting_color=SECOND)

    assert as_second is None
    assert as_first is ForbiddenType.DOUBLE_THREE


def test_constrained_color_is_the_starting_color(renju: RenjuRule) -> None:
    assert renju.constrained_color(FIRST) is FIRST
    assert renju.constrained_color(SECOND) is SECOND


# ----------------------------------------------------------------------
# board size independence
# ----------------------------------------------------------------------
def test_double_three_on_a_19x19_board(renju: RenjuRule) -> None:
    board = build_board(
        size=19, white=[(9, 11), (9, 12), (9, 13), (11, 13), (10, 13)]
    )

    assert classify(renju, board, 9, 13) is ForbiddenType.DOUBLE_THREE


def test_five_on_the_far_edge_of_a_19x19_board(renju: RenjuRule) -> None:
    board = build_board(
        size=19, white=[(14, 18), (15, 18), (16, 18), (17, 18), (18, 18)]
    )

    assert judge(renju, board, 18, 18).win is True


# ----------------------------------------------------------------------
# free style rule
# ----------------------------------------------------------------------
def test_free_style_has_no_forbidden_moves() -> None:
    rule = FreeStyleRule(DEFAULT_WIN_LENGTH)
    board = build_board(white=[(5, 7), (6, 7), (7, 7), (7, 5), (7, 6)])

    assert rule.evaluate_move(board, 7, 7, FIRST, FIRST).win is False
    assert rule.classify_forbidden(board, 9, 9, FIRST, FIRST) is None
    assert rule.forbidden_points(board, FIRST) == {}
    assert rule.constrained_color(FIRST) is None


def test_free_style_accepts_an_overline_as_a_win() -> None:
    rule = FreeStyleRule(DEFAULT_WIN_LENGTH)
    board = build_board(white=[(3, 7), (4, 7), (5, 7), (6, 7), (7, 7), (8, 7)])

    assert rule.evaluate_move(board, 7, 7, FIRST, FIRST).win is True


def test_rule_names() -> None:
    assert RenjuRule().name == "renju"
    assert FreeStyleRule().name == "free_style"
