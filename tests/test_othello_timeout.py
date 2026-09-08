"""Game-specific turn-timeout behavior."""

from __future__ import annotations

import pytest

from app.board import Color
from app.config import GameSettings
from app.errors import TurnExpiredError
from app.game import GameStatus
from app.game_type import GameType
from app.protocol import (
    CreateRoomCommand,
    connected,
    game_state,
    parse_client_message,
    turn_timeout,
)
from app.room import GameRoom, TurnTimeoutResult
from app.room_manager import RoomManager


async def playing_room(game_type: GameType, limit: int = 5) -> GameRoom:
    if game_type is GameType.OTHELLO:
        room = GameRoom.from_settings(
            "timeout-room",
            GameSettings(
                game_type=GameType.OTHELLO,
                board_size=8,
                win_length=None,
                starting_color=Color.BLACK,
                turn_time_limit_sec=limit,
            ),
        )
    else:
        room = GameRoom("timeout-room", turn_time_limit_sec=limit)

    await room.join("first")
    await room.join("second")
    await room.become_player("first")
    await room.become_player("second")
    await room.set_ready("first")
    await room.set_ready("second")
    assert room.game.status is GameStatus.PLAYING
    return room


def test_othello_settings_accept_a_turn_timer() -> None:
    settings = GameSettings(
        game_type=GameType.OTHELLO,
        board_size=8,
        win_length=None,
        starting_color=Color.BLACK,
        turn_time_limit_sec=10,
    )

    assert settings.turn_time_limit_sec == 10


def test_connected_advertises_game_specific_timeout_actions() -> None:
    options = connected(GameSettings())["room_creation_options"]

    assert options == {
        "GOMOKU": {
            "turn_time_limits": [None, 5, 10, 15, 30, 60],
            "timeout_action": "SKIP_TURN",
        },
        "OTHELLO": {
            "turn_time_limits": [None, 5, 10, 15, 30, 60],
            "timeout_action": "RANDOM_LEGAL_MOVE",
        },
    }


def test_othello_game_state_uses_the_room_timer_setting() -> None:
    settings = GameSettings(
        game_type=GameType.OTHELLO,
        board_size=8,
        win_length=None,
        starting_color=Color.BLACK,
        turn_time_limit_sec=10,
    )
    room = GameRoom.from_settings("state-room", settings)

    state = game_state(room.game, settings=room.settings)

    assert state["game_type"] == "OTHELLO"
    assert state["turn_time_limit_sec"] == 10


def test_parse_othello_room_accepts_a_turn_timer() -> None:
    command = parse_client_message(
        '{"type":"create_room","room_name":"Timed Othello",'
        '"game_type":"OTHELLO","turn_time_limit_sec":10}'
    )

    assert command == CreateRoomCommand(
        room_name="Timed Othello",
        game_type=GameType.OTHELLO,
        turn_time_limit_sec=10,
    )


async def test_room_manager_keeps_the_othello_turn_timer() -> None:
    manager = RoomManager(GameSettings())

    room, _ = await manager.create_room(
        "creator", "Timed Othello", GameType.OTHELLO, 10
    )

    assert room.game_type is GameType.OTHELLO
    assert room.turn_time_limit_sec == 10
    assert room.settings.turn_time_limit_sec == 10


async def test_othello_timeout_plays_a_random_legal_move(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr("app.room.time.monotonic", lambda: now[0])
    chosen: list[tuple[int, int]] = []

    def choose_first(moves: list[tuple[int, int]]) -> tuple[int, int]:
        chosen.extend(moves)
        return moves[0]

    monkeypatch.setattr("app.room.secrets.choice", choose_first)
    room = await playing_room(GameType.OTHELLO)
    revision = room.turn_revision
    initial_legal_moves = room.game.legal_moves_for_current_turn

    now[0] = 106.0
    result = await room.expire_turn(revision)

    assert result is not None
    assert result.timed_out_color is Color.BLACK
    assert result.automatic_move is not None
    assert (result.automatic_move.x, result.automatic_move.y) == initial_legal_moves[0]
    assert chosen == initial_legal_moves
    assert room.game.move_count == 5
    assert room.game.current_turn is result.current_turn
    assert room.turn_remaining_ms == 5_000


async def test_gomoku_timeout_still_skips_without_placing(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr("app.room.time.monotonic", lambda: now[0])
    room = await playing_room(GameType.GOMOKU)
    revision = room.turn_revision

    now[0] = 106.0
    result = await room.expire_turn(revision)

    assert result == TurnTimeoutResult(Color.WHITE, Color.BLACK)
    assert room.game.move_count == 0
    assert room.game.current_turn is Color.BLACK


async def test_late_othello_move_preserves_the_automatic_result(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr("app.room.time.monotonic", lambda: now[0])
    monkeypatch.setattr("app.room.secrets.choice", lambda moves: moves[-1])
    room = await playing_room(GameType.OTHELLO)
    requested_move = room.game.legal_moves_for_current_turn[0]

    now[0] = 106.0
    with pytest.raises(TurnExpiredError) as excinfo:
        await room.make_move("first", *requested_move)

    timeout_result = excinfo.value.timeout_result
    assert isinstance(timeout_result, TurnTimeoutResult)
    assert timeout_result.automatic_move is not None
    assert room.game.move_count == 5


def test_othello_timeout_message_identifies_the_automatic_move() -> None:
    room = GameRoom.from_settings(
        "message-room",
        GameSettings(
            game_type=GameType.OTHELLO,
            board_size=8,
            win_length=None,
            starting_color=Color.BLACK,
        ),
    )
    room.game.start()
    move = room.game.make_move(Color.BLACK, 2, 3)

    assert turn_timeout(GameType.OTHELLO, Color.BLACK, Color.WHITE, move) == {
        "type": "turn_timeout",
        "game_type": "OTHELLO",
        "timed_out_color": "BLACK",
        "current_turn": "WHITE",
        "action": "RANDOM_LEGAL_MOVE",
        "move": {"x": 2, "y": 3},
    }
