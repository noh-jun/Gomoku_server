"""Unit tests for the pure game logic (no network involved).

Default setup: WHITE opens the game and is bound by the Renju forbidden
moves; BLACK plays second and is unconstrained.
"""

from __future__ import annotations

import pytest

from app.board import Color
from app.config import (
    DEFAULT_BOARD_SIZE,
    DEFAULT_STARTING_COLOR,
    DEFAULT_WIN_LENGTH,
    GameSettings,
)
from app.errors import (
    ForbiddenMoveError,
    GameAlreadyFinishedError,
    GameNotStartedError,
    InvalidMoveError,
    NotYourTurnError,
    OutOfRangeError,
    PositionOccupiedError,
)
from app.game import GameOverReason, GameStatus, GomokuGame, MoveResult
from app.rules import ForbiddenType, FreeStyleRule, RenjuRule

#: Scattered filler moves; none of them touches another one, so they can
#: never build a three, a four or a five of their own.
FILLERS: list[tuple[int, int]] = [
    (0, 0),
    (14, 0),
    (0, 14),
    (14, 14),
    (7, 0),
    (0, 7),
    (14, 7),
    (7, 14),
]


@pytest.fixture
def game() -> GomokuGame:
    return GomokuGame()


def play_white(game: GomokuGame, moves: list[tuple[int, int]]) -> MoveResult:
    """WHITE (starting player) plays ``moves``, BLACK answers in the corners."""
    fillers = iter(FILLERS)
    result: MoveResult | None = None
    for x, y in moves:
        result = game.make_move(Color.WHITE, x, y)
        if result.is_game_over:
            break
        fx, fy = next(fillers)
        game.make_move(Color.BLACK, fx, fy)
    assert result is not None
    return result


def setup_white(game: GomokuGame, moves: list[tuple[int, int]]) -> None:
    """Play ``moves`` for WHITE with BLACK answering in the corners.

    Unlike :func:`play_white` this never expects a result, so it is the way
    to build the position *in front of* a point WHITE may not play.
    """
    fillers = iter(FILLERS)
    for x, y in moves:
        game.make_move(Color.WHITE, x, y)
        fx, fy = next(fillers)
        game.make_move(Color.BLACK, fx, fy)


def play_black(game: GomokuGame, moves: list[tuple[int, int]]) -> MoveResult:
    """BLACK (second player) plays ``moves``, WHITE answers in the corners."""
    fillers = iter(FILLERS)
    result: MoveResult | None = None
    for x, y in moves:
        fx, fy = next(fillers)
        game.make_move(Color.WHITE, fx, fy)
        result = game.make_move(Color.BLACK, x, y)
        if result.is_game_over:
            break
    assert result is not None
    return result


# ----------------------------------------------------------------------
# basic moves and turn order
# ----------------------------------------------------------------------
def test_initial_state(game: GomokuGame) -> None:
    assert game.status is GameStatus.PLAYING
    assert game.starting_color is Color.WHITE
    assert game.starting_color is DEFAULT_STARTING_COLOR
    assert game.current_turn is Color.WHITE
    assert game.winner is None
    assert game.loser is None
    assert game.reason is None
    assert game.forbidden_moves == {}
    assert game.constrained_color is Color.WHITE
    assert game.rule_name == "renju"
    assert game.board_size == DEFAULT_BOARD_SIZE
    assert game.win_length == DEFAULT_WIN_LENGTH
    assert all(cell is None for row in game.board for cell in row)


def test_white_moves_first(game: GomokuGame) -> None:
    result = game.make_move(Color.WHITE, 7, 7)

    assert result.color is Color.WHITE
    assert (result.x, result.y) == (7, 7)
    assert result.accepted is True
    assert result.next_turn is Color.BLACK
    assert game.stone_at(7, 7) is Color.WHITE
    assert game.current_turn is Color.BLACK
    assert game.move_count == 1


def test_black_cannot_open_the_game(game: GomokuGame) -> None:
    with pytest.raises(NotYourTurnError) as excinfo:
        game.make_move(Color.BLACK, 7, 7)

    assert excinfo.value.code == "NOT_YOUR_TURN"
    assert game.current_turn is Color.WHITE
    assert game.move_count == 0


def test_turn_alternates(game: GomokuGame) -> None:
    game.make_move(Color.WHITE, 7, 7)
    result = game.make_move(Color.BLACK, 7, 8)

    assert result.color is Color.BLACK
    assert result.next_turn is Color.WHITE
    assert game.current_turn is Color.WHITE


def test_starting_color_is_the_constrained_one(game: GomokuGame) -> None:
    assert game.is_constrained(Color.WHITE) is True
    assert game.is_constrained(Color.BLACK) is False


def test_move_on_occupied_position_is_rejected(game: GomokuGame) -> None:
    game.make_move(Color.WHITE, 7, 7)

    with pytest.raises(PositionOccupiedError) as excinfo:
        game.make_move(Color.BLACK, 7, 7)

    assert excinfo.value.code == "POSITION_OCCUPIED"
    assert game.current_turn is Color.BLACK
    assert game.move_count == 1


@pytest.mark.parametrize("x,y", [(-1, 0), (0, -1), (15, 7), (7, 15), (99, 99)])
def test_out_of_range_move_is_rejected(game: GomokuGame, x: int, y: int) -> None:
    with pytest.raises(OutOfRangeError) as excinfo:
        game.make_move(Color.WHITE, x, y)

    assert excinfo.value.code == "OUT_OF_RANGE"
    assert game.move_count == 0


@pytest.mark.parametrize("x,y", [("7", 7), (7.0, 7), (None, 3), (True, 1), (3, [1])])
def test_non_integer_coordinates_are_rejected(
    game: GomokuGame, x: object, y: object
) -> None:
    with pytest.raises(InvalidMoveError) as excinfo:
        game.make_move(Color.WHITE, x, y)

    assert excinfo.value.code == "INVALID_MOVE"
    assert game.move_count == 0


def test_move_before_start_is_rejected() -> None:
    waiting = GomokuGame(status=GameStatus.WAITING)

    with pytest.raises(GameNotStartedError) as excinfo:
        waiting.make_move(Color.WHITE, 7, 7)

    assert excinfo.value.code == "GAME_NOT_STARTED"


# ----------------------------------------------------------------------
# winning
# ----------------------------------------------------------------------
def test_white_wins_with_exactly_five(game: GomokuGame) -> None:
    result = play_white(game, [(3, 7), (4, 7), (5, 7), (6, 7), (7, 7)])

    assert result.winner is Color.WHITE
    assert result.loser is Color.BLACK
    assert result.reason is GameOverReason.FIVE_IN_A_ROW
    assert result.next_turn is None
    assert result.status is GameStatus.FINISHED

    assert game.winner is Color.WHITE
    assert game.loser is Color.BLACK
    assert game.status is GameStatus.FINISHED
    # A finished game has no turn, so clients cannot show a stale turn.
    assert game.current_turn is None


def test_vertical_win(game: GomokuGame) -> None:
    result = play_white(game, [(7, 3), (7, 4), (7, 5), (7, 6), (7, 7)])

    assert result.winner is Color.WHITE
    assert result.reason is GameOverReason.FIVE_IN_A_ROW


def test_backslash_diagonal_win(game: GomokuGame) -> None:
    result = play_white(game, [(3, 3), (4, 4), (5, 5), (6, 6), (7, 7)])

    assert result.winner is Color.WHITE


def test_slash_diagonal_win(game: GomokuGame) -> None:
    result = play_white(game, [(3, 7), (4, 6), (5, 5), (6, 4), (7, 3)])

    assert result.winner is Color.WHITE


def test_win_counting_both_directions(game: GomokuGame) -> None:
    """The last stone completes the row from the middle: ``2 + 1 + 2``."""
    result = play_white(game, [(5, 7), (6, 7), (8, 7), (9, 7), (7, 7)])

    assert result.winner is Color.WHITE
    assert (result.x, result.y) == (7, 7)


def test_black_wins_with_five(game: GomokuGame) -> None:
    result = play_black(game, [(3, 9), (4, 9), (5, 9), (6, 9), (7, 9)])

    assert result.winner is Color.BLACK
    assert result.loser is Color.WHITE
    assert result.reason is GameOverReason.FIVE_IN_A_ROW


def test_black_may_win_with_six(game: GomokuGame) -> None:
    """The second player is not bound by the overline rule."""
    result = play_black(game, [(2, 9), (3, 9), (4, 9), (5, 9), (7, 9), (6, 9)])

    assert result.winner is Color.BLACK
    assert result.reason is GameOverReason.FIVE_IN_A_ROW


def test_four_in_a_row_does_not_win(game: GomokuGame) -> None:
    result = play_white(game, [(3, 7), (4, 7), (5, 7), (6, 7)])

    assert result.winner is None
    assert result.status is GameStatus.PLAYING
    assert result.next_turn is Color.BLACK


def test_check_win_helper(game: GomokuGame) -> None:
    play_white(game, [(3, 7), (4, 7), (5, 7), (6, 7), (7, 7)])

    assert game.check_win(7, 7) is True
    assert game.check_win(0, 0) is False
    assert game.check_win(99, 99) is False


# ----------------------------------------------------------------------
# forbidden points of the starting player: blocked, not punished
# ----------------------------------------------------------------------
#: Position in front of a forbidden point, the point, and why it is forbidden.
FORBIDDEN_SETUPS: list[
    tuple[list[tuple[int, int]], tuple[int, int], ForbiddenType]
] = [
    # A sixth stone in the row would be an overline (장목).
    ([(3, 7), (4, 7), (5, 7), (6, 7), (8, 7)], (7, 7), ForbiddenType.OVERLINE),
    ([(5, 7), (6, 7), (7, 5), (7, 6)], (7, 7), ForbiddenType.DOUBLE_THREE),
    (
        [(4, 7), (5, 7), (6, 7), (7, 4), (7, 5), (7, 6)],
        (7, 7),
        ForbiddenType.DOUBLE_FOUR,
    ),
]


@pytest.mark.parametrize("setup,point,expected", FORBIDDEN_SETUPS)
def test_white_may_not_play_a_forbidden_point(
    game: GomokuGame,
    setup: list[tuple[int, int]],
    point: tuple[int, int],
    expected: ForbiddenType,
) -> None:
    setup_white(game, setup)
    x, y = point

    with pytest.raises(ForbiddenMoveError) as excinfo:
        game.make_move(Color.WHITE, x, y)

    assert excinfo.value.code == "FORBIDDEN_MOVE"
    assert excinfo.value.forbidden_type is expected
    assert (excinfo.value.x, excinfo.value.y) == point
    assert game.forbidden_moves[point] is expected


def test_a_rejected_move_changes_nothing(game: GomokuGame) -> None:
    """The stone is never placed, so the position stays bit for bit the same."""
    setup_white(game, [(5, 7), (6, 7), (7, 5), (7, 6)])
    board_before = game.board_snapshot()
    moves_before = game.move_count
    last_before = game.last_move

    with pytest.raises(ForbiddenMoveError):
        game.make_move(Color.WHITE, 7, 7)

    assert game.board_snapshot() == board_before
    assert game.move_count == moves_before
    assert game.last_move == last_before
    assert game.current_turn is Color.WHITE
    assert game.status is GameStatus.PLAYING
    assert game.winner is None
    assert game.loser is None
    assert game.reason is None


def test_the_game_continues_after_a_rejection(game: GomokuGame) -> None:
    """It is still WHITE to move, and any legal point is still accepted."""
    setup_white(game, [(5, 7), (6, 7), (7, 5), (7, 6)])

    for _ in range(3):
        with pytest.raises(ForbiddenMoveError):
            game.make_move(Color.WHITE, 7, 7)

    result = game.make_move(Color.WHITE, 2, 2)

    assert result.status is GameStatus.PLAYING
    assert result.next_turn is Color.BLACK
    assert game.stone_at(2, 2) is Color.WHITE
    assert game.stone_at(7, 7) is None


def test_a_five_that_is_also_a_double_four_still_wins(game: GomokuGame) -> None:
    """Making five is never punished, so the point is playable and it wins."""
    setup_white(game, [(3, 7), (4, 7), (5, 7), (6, 7), (7, 4), (7, 5), (7, 6)])

    assert (7, 7) not in game.forbidden_moves

    result = game.make_move(Color.WHITE, 7, 7)

    assert result.winner is Color.WHITE
    assert result.reason is GameOverReason.FIVE_IN_A_ROW


def test_forbidden_moves_track_the_position(game: GomokuGame) -> None:
    assert game.forbidden_moves == {}

    setup_white(game, [(5, 7), (6, 7), (7, 5), (7, 6)])

    assert game.forbidden_moves[(7, 7)] is ForbiddenType.DOUBLE_THREE
    assert all(game.stone_at(x, y) is None for x, y in game.forbidden_moves)


def test_a_finished_game_has_no_forbidden_points(game: GomokuGame) -> None:
    play_white(game, [(3, 7), (4, 7), (5, 7), (6, 7), (7, 7)])

    assert game.status is GameStatus.FINISHED
    assert game.forbidden_moves == {}


def test_the_renju_check_comes_last(game: GomokuGame) -> None:
    """Everything else about a move is reported before the restriction is."""
    setup_white(game, [(5, 7), (6, 7), (7, 5), (7, 6)])
    assert (7, 7) in game.forbidden_moves

    with pytest.raises(NotYourTurnError):
        game.make_move(Color.BLACK, 7, 7)
    with pytest.raises(OutOfRangeError):
        game.make_move(Color.WHITE, 99, 99)
    with pytest.raises(InvalidMoveError):
        game.make_move(Color.WHITE, 7.0, 7)
    with pytest.raises(ForbiddenMoveError):
        game.make_move(Color.WHITE, 7, 7)


def test_an_occupied_forbidden_point_is_reported_as_occupied(
    game: GomokuGame,
) -> None:
    """Occupancy is checked before the Renju restriction."""
    setup_white(game, [(5, 7), (6, 7), (7, 5), (7, 6)])
    assert (7, 7) in game.forbidden_moves

    game.make_move(Color.WHITE, 2, 2)
    game.make_move(Color.BLACK, 7, 7)

    with pytest.raises(PositionOccupiedError):
        game.make_move(Color.WHITE, 7, 7)


def test_black_is_not_bound_by_the_double_three_rule(game: GomokuGame) -> None:
    """The same double-three shape is a plain move for the second player."""
    result = play_black(game, [(7, 9), (8, 9), (9, 7), (9, 8), (9, 9)])

    assert result.status is GameStatus.PLAYING
    assert result.next_turn is Color.WHITE
    assert game.forbidden_moves == {}


# ----------------------------------------------------------------------
# finished games
# ----------------------------------------------------------------------
def test_no_move_is_accepted_after_a_win(game: GomokuGame) -> None:
    play_white(game, [(3, 7), (4, 7), (5, 7), (6, 7), (7, 7)])
    board_before = game.board_snapshot()

    for color in (Color.BLACK, Color.WHITE):
        with pytest.raises(GameAlreadyFinishedError) as excinfo:
            game.make_move(color, 1, 1)
        assert excinfo.value.code == "GAME_ALREADY_FINISHED"

    assert game.board_snapshot() == board_before


def test_a_finished_game_is_reported_before_anything_else(
    game: GomokuGame,
) -> None:
    """Even a point that used to be forbidden reports the finished game."""
    setup_white(game, [(5, 7), (6, 7), (7, 5), (7, 6)])
    assert (7, 7) in game.forbidden_moves

    # WHITE wins on a diagonal far away from the forbidden point.  The
    # fillers continue where ``setup_white`` stopped, so nothing collides.
    fillers = iter(FILLERS[4:])
    for x, y in [(2, 2), (3, 3), (4, 4), (5, 5)]:
        game.make_move(Color.WHITE, x, y)
        fx, fy = next(fillers)
        game.make_move(Color.BLACK, fx, fy)
    game.make_move(Color.WHITE, 6, 6)
    assert game.status is GameStatus.FINISHED

    with pytest.raises(GameAlreadyFinishedError):
        game.make_move(Color.WHITE, 7, 7)


def test_draw_when_board_is_full() -> None:
    """A 4x4 board can hold neither five in a row nor any Renju shape."""
    small = GomokuGame(board_size=4)
    result: MoveResult | None = None
    for y in range(4):
        for x in range(4):
            turn = small.current_turn
            assert turn is not None
            result = small.make_move(turn, x, y)

    assert result is not None
    assert small.is_board_full() is True
    assert small.is_draw() is True
    assert result.winner is None
    assert result.loser is None
    assert result.reason is GameOverReason.DRAW
    assert result.next_turn is None
    assert small.current_turn is None
    assert small.status is GameStatus.FINISHED


def test_is_draw_is_false_while_the_board_has_space(game: GomokuGame) -> None:
    game.make_move(Color.WHITE, 7, 7)

    assert game.is_draw() is False
    assert game.is_board_full() is False


# ----------------------------------------------------------------------
# reset / restart
# ----------------------------------------------------------------------
def test_reset_clears_the_result(game: GomokuGame) -> None:
    play_white(game, [(3, 7), (4, 7), (5, 7), (6, 7), (7, 7)])
    game.reset()

    assert game.status is GameStatus.PLAYING
    assert game.current_turn is Color.WHITE
    assert game.starting_color is Color.WHITE
    assert game.winner is None
    assert game.loser is None
    assert game.reason is None
    assert game.forbidden_moves == {}
    assert game.move_count == 0
    assert game.last_move is None
    assert all(cell is None for row in game.board for cell in row)
    assert game.make_move(Color.WHITE, 7, 7).color is Color.WHITE


def test_reset_can_change_the_starting_color(game: GomokuGame) -> None:
    game.reset(starting_color=Color.BLACK)

    assert game.starting_color is Color.BLACK
    assert game.current_turn is Color.BLACK
    assert game.is_constrained(Color.BLACK) is True
    assert game.is_constrained(Color.WHITE) is False

    # Now BLACK opens, and BLACK is the one blocked by the Renju rules.
    fillers = iter(FILLERS)
    for x, y in [(5, 7), (6, 7), (7, 5), (7, 6)]:
        game.make_move(Color.BLACK, x, y)
        fx, fy = next(fillers)
        game.make_move(Color.WHITE, fx, fy)

    assert game.forbidden_moves[(7, 7)] is ForbiddenType.DOUBLE_THREE
    with pytest.raises(ForbiddenMoveError):
        game.make_move(Color.BLACK, 7, 7)


def test_reset_can_park_the_game_in_waiting(game: GomokuGame) -> None:
    game.reset(status=GameStatus.WAITING)

    assert game.status is GameStatus.WAITING
    with pytest.raises(GameNotStartedError):
        game.make_move(Color.WHITE, 7, 7)


class StuckRule(RenjuRule):
    """Declares every empty point forbidden once two stones are down.

    A position where the constrained player genuinely has nowhere to go is
    not reachable by hand on a real board, so the scan is stubbed instead.
    The two stone delay keeps the opening playable.
    """

    def forbidden_points(self, board, starting_color):  # type: ignore[no-untyped-def]
        placed = sum(1 for row in board for cell in row if cell is not None)
        if placed < 2:
            return {}
        return {
            (x, y): ForbiddenType.DOUBLE_THREE
            for y, row in enumerate(board)
            for x, cell in enumerate(row)
            if cell is None
        }


def test_a_stuck_constrained_player_loses() -> None:
    """Blocking forbidden points keeps the result of a truly dead position."""
    stuck = GomokuGame(board_size=4, rule=StuckRule(DEFAULT_WIN_LENGTH))

    stuck.make_move(Color.WHITE, 0, 0)
    result = stuck.make_move(Color.BLACK, 1, 1)

    assert result.reason is GameOverReason.NO_FORBIDDEN_FREE_MOVE
    assert result.loser is Color.WHITE
    assert result.winner is Color.BLACK
    assert result.next_turn is None
    assert stuck.status is GameStatus.FINISHED
    assert stuck.forbidden_moves == {}


def test_only_the_constrained_player_can_get_stuck() -> None:
    """With BLACK opening, BLACK is the one who can run out of points."""
    stuck = GomokuGame(
        board_size=4,
        starting_color=Color.BLACK,
        rule=StuckRule(DEFAULT_WIN_LENGTH),
    )

    stuck.make_move(Color.BLACK, 0, 0)
    result = stuck.make_move(Color.WHITE, 1, 1)

    assert result.reason is GameOverReason.NO_FORBIDDEN_FREE_MOVE
    assert result.loser is Color.BLACK
    assert result.winner is Color.WHITE


def test_suspend_only_affects_a_running_game(game: GomokuGame) -> None:
    game.suspend()
    assert game.status is GameStatus.WAITING

    game.reset()
    play_white(game, [(3, 7), (4, 7), (5, 7), (6, 7), (7, 7)])
    game.suspend()
    assert game.status is GameStatus.FINISHED


def test_start_restores_the_turn() -> None:
    waiting = GomokuGame(status=GameStatus.WAITING)
    waiting.current_turn = None

    waiting.start()

    assert waiting.status is GameStatus.PLAYING
    assert waiting.current_turn is Color.WHITE


# ----------------------------------------------------------------------
# board geometry (15 x 15 / 19 x 19)
# ----------------------------------------------------------------------
def test_19x19_board_is_fully_playable() -> None:
    game = GomokuGame(board_size=19)

    assert game.board_size == 19
    assert len(game.board) == 19
    assert all(len(row) == 19 for row in game.board)
    assert game.intersection_count == 361

    result = game.make_move(Color.WHITE, 18, 18)
    assert (result.x, result.y) == (18, 18)
    assert game.stone_at(18, 18) is Color.WHITE


def test_coordinate_15_depends_on_the_board_size() -> None:
    big = GomokuGame(board_size=19)
    assert big.make_move(Color.WHITE, 15, 15).color is Color.WHITE

    small = GomokuGame(board_size=15)
    with pytest.raises(OutOfRangeError):
        small.make_move(Color.WHITE, 15, 15)


@pytest.mark.parametrize("x,y", [(19, 0), (0, 19), (20, 20), (-1, 5)])
def test_out_of_range_on_19x19(x: int, y: int) -> None:
    game = GomokuGame(board_size=19)

    with pytest.raises(OutOfRangeError):
        game.make_move(Color.WHITE, x, y)


def test_rules_are_size_independent() -> None:
    game = GomokuGame(board_size=19)
    fillers = iter([(0, 0), (1, 0), (3, 0), (5, 0), (7, 0)])
    result: MoveResult | None = None
    for x, y in [(14, 18), (15, 18), (16, 18), (17, 18), (18, 18)]:
        result = game.make_move(Color.WHITE, x, y)
        if result.is_game_over:
            break
        fx, fy = next(fillers)
        game.make_move(Color.BLACK, fx, fy)

    assert result is not None
    assert result.winner is Color.WHITE
    assert result.reason is GameOverReason.FIVE_IN_A_ROW


def test_reset_preserves_the_board_size() -> None:
    game = GomokuGame(board_size=19)
    game.make_move(Color.WHITE, 18, 18)

    game.reset()

    assert game.board_size == 19
    assert len(game.board) == 19
    assert game.make_move(Color.WHITE, 18, 18).color is Color.WHITE


def test_settings_round_trip() -> None:
    settings = GameSettings(board_size=19, win_length=5, starting_color=Color.WHITE)
    game = GomokuGame.from_settings(settings)

    assert game.settings == settings
    assert game.board_size == 19
    assert game.starting_color is Color.WHITE


@pytest.mark.parametrize(
    "kwargs", [{"board_size": 0}, {"board_size": -3}, {"win_length": 0}]
)
def test_invalid_geometry_is_rejected(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        GomokuGame(**kwargs)


# ----------------------------------------------------------------------
# alternative rule engines
# ----------------------------------------------------------------------
def test_free_style_rule_has_no_forbidden_moves() -> None:
    game = GomokuGame(rule=FreeStyleRule(DEFAULT_WIN_LENGTH))

    assert game.rule_name == "free_style"
    assert game.constrained_color is None
    # The same shape that is blocked under Renju is a normal move here.
    result = play_white(game, [(5, 7), (6, 7), (7, 5), (7, 6), (7, 7)])
    assert result.status is GameStatus.PLAYING
    assert game.forbidden_moves == {}


def test_free_style_rule_lets_the_starting_player_win_with_six() -> None:
    game = GomokuGame(rule=FreeStyleRule(DEFAULT_WIN_LENGTH))

    result = play_white(game, [(3, 7), (4, 7), (5, 7), (6, 7), (8, 7), (7, 7)])

    assert result.winner is Color.WHITE
    assert result.reason is GameOverReason.FIVE_IN_A_ROW


def test_injected_rule_owns_the_win_length() -> None:
    game = GomokuGame(win_length=5, rule=FreeStyleRule(3))

    assert game.win_length == 3
    result = play_white(game, [(3, 7), (4, 7), (5, 7)])
    assert result.winner is Color.WHITE


def test_renju_rule_win_length_follows_the_game() -> None:
    game = GomokuGame(win_length=4, rule=RenjuRule(4))

    assert game.win_length == 4
    result = play_white(game, [(3, 7), (4, 7), (5, 7), (6, 7)])
    assert result.winner is Color.WHITE
    assert result.reason is GameOverReason.FIVE_IN_A_ROW


def test_color_opponent() -> None:
    assert Color.BLACK.opponent is Color.WHITE
    assert Color.WHITE.opponent is Color.BLACK
