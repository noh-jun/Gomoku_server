"""Unit tests for the pure Othello engine."""

import pytest

from app.board import Color
from app.errors import InvalidMoveError, PositionOccupiedError
from app.game import GameOverReason, GameStatus
from app.othello import OthelloGame


def test_initial_position_and_legal_moves() -> None:
    game = OthelloGame()

    assert game.current_turn is Color.BLACK
    assert game.score == {Color.BLACK: 2, Color.WHITE: 2}
    assert game.stone_at(3, 3) is Color.WHITE
    assert game.stone_at(4, 3) is Color.BLACK
    assert set(game.legal_moves_for_current_turn) == {
        (2, 3), (3, 2), (4, 5), (5, 4)
    }


def test_move_flips_stones_and_changes_turn() -> None:
    game = OthelloGame()

    result = game.make_move(Color.BLACK, 2, 3)

    assert game.stone_at(2, 3) is Color.BLACK
    assert game.stone_at(3, 3) is Color.BLACK
    assert [(cell.x, cell.y, cell.color) for cell in result.flipped] == [
        (3, 3, Color.BLACK)
    ]
    assert result.next_turn is Color.WHITE
    assert result.score == {Color.BLACK: 4, Color.WHITE: 1}


def test_move_must_flip_and_cannot_use_occupied_position() -> None:
    game = OthelloGame()

    with pytest.raises(InvalidMoveError):
        game.make_move(Color.BLACK, 0, 0)
    with pytest.raises(PositionOccupiedError):
        game.make_move(Color.BLACK, 3, 3)


def test_complete_deterministic_game_handles_pass_and_finishes() -> None:
    game = OthelloGame()
    passes: list[Color] = []

    while game.status is GameStatus.PLAYING:
        color = game.current_turn
        assert color is not None
        x, y = game.legal_moves_for_current_turn[0]
        result = game.make_move(color, x, y)
        if result.passed_color is not None:
            passes.append(result.passed_color)

    assert passes
    assert game.reason in {GameOverReason.BOARD_FULL, GameOverReason.NO_LEGAL_MOVES}
    assert sum(game.score.values()) == game.move_count
    assert game.current_turn is None
    assert game.winner is Color.WHITE
    assert game.loser is Color.BLACK


def test_reset_restores_the_four_starting_stones() -> None:
    game = OthelloGame()
    game.make_move(Color.BLACK, 2, 3)

    game.reset(status=GameStatus.WAITING)

    assert game.status is GameStatus.WAITING
    assert game.score == {Color.BLACK: 2, Color.WHITE: 2}
    assert game.current_turn is Color.BLACK
    assert game.legal_moves_for_current_turn == []


def test_game_ends_when_neither_color_has_a_legal_move() -> None:
    game = OthelloGame()
    game.board = [[Color.BLACK for _ in range(8)] for _ in range(8)]
    game.board[0][0] = None
    game.board[0][1] = Color.WHITE
    game.board[7][7] = None
    game.move_count = 62

    result = game.make_move(Color.BLACK, 0, 0)

    assert result.reason is GameOverReason.NO_LEGAL_MOVES
    assert result.winner is Color.BLACK
    assert result.score == {Color.BLACK: 63, Color.WHITE: 0}
    assert game.stone_at(7, 7) is None
