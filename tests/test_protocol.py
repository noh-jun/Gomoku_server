"""Tests for the JSON protocol: parsing and message building."""

from __future__ import annotations

import json

import pytest

from app.board import Color
from app.config import GameSettings
from app.errors import ForbiddenMoveError
from app.game import GameOverReason, GameStatus, GomokuGame, MoveResult
from app.game_type import GameType
from app.othello import OthelloGame
from app.room import GameRoom, RoomSummary
from app.protocol import (
    CreateRoomCommand,
    ErrorCode,
    GetRoomListCommand,
    JoinRoomCommand,
    LeaveRoomCommand,
    MoveCommand,
    PingCommand,
    ProtocolError,
    RestartRequestCommand,
    connected,
    describe_game_over,
    error,
    forbidden_move_error,
    forbidden_moves,
    game_over,
    game_start,
    game_state,
    joined,
    left_room,
    move_result,
    parse_client_message,
    player_disconnected,
    player_joined,
    pong,
    restart,
    restart_requested,
    room_created,
    room_list,
    settings_fields,
)
from app.rules import ForbiddenType

SETTINGS = GameSettings(board_size=19, win_length=5, starting_color=Color.WHITE)

FILLERS = [(0, 0), (14, 0), (0, 14), (14, 14)]


def finished_game(moves: list[tuple[int, int]]) -> GomokuGame:
    """Play ``moves`` for WHITE with harmless BLACK answers in between."""
    game = GomokuGame()
    for index, (x, y) in enumerate(moves):
        result = game.make_move(Color.WHITE, x, y)
        if result.is_game_over:
            break
        game.make_move(Color.BLACK, *FILLERS[index])
    return game


# ----------------------------------------------------------------------
# client -> server
# ----------------------------------------------------------------------
def test_parse_move() -> None:
    command = parse_client_message(json.dumps({"type": "move", "x": 7, "y": 8}))

    assert command == MoveCommand(x=7, y=8)


def test_parse_move_ignores_extra_fields() -> None:
    raw = json.dumps(
        {
            "type": "move",
            "x": 1,
            "y": 2,
            "color": "BLACK",
            "board_size": 99,
            "starting_color": "BLACK",
        }
    )

    assert parse_client_message(raw) == MoveCommand(x=1, y=2)


def test_parse_ping_and_restart() -> None:
    assert parse_client_message('{"type": "ping"}') == PingCommand()
    assert (
        parse_client_message('{"type": "restart_request"}')
        == RestartRequestCommand()
    )


@pytest.mark.parametrize(
    "raw", ["", "{", "not json", "[]", '"text"', "42", '{"no_type": 1}', '{"type": 5}']
)
def test_invalid_messages(raw: str) -> None:
    with pytest.raises(ProtocolError) as excinfo:
        parse_client_message(raw)

    assert excinfo.value.code is ErrorCode.INVALID_MESSAGE


def test_unknown_message_type() -> None:
    with pytest.raises(ProtocolError) as excinfo:
        parse_client_message('{"type": "surrender"}')

    assert excinfo.value.code is ErrorCode.UNKNOWN_MESSAGE_TYPE


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "move"},
        {"type": "move", "x": "1", "y": 2},
        {"type": "move", "x": 1.5, "y": 2},
        {"type": "move", "x": True, "y": 2},
        {"type": "move", "x": None, "y": None},
    ],
)
def test_move_needs_integer_coordinates(payload: dict[str, object]) -> None:
    with pytest.raises(ProtocolError) as excinfo:
        parse_client_message(json.dumps(payload))

    assert excinfo.value.code is ErrorCode.INVALID_MOVE


# ----------------------------------------------------------------------
# configuration fields
# ----------------------------------------------------------------------
def test_settings_fields() -> None:
    assert settings_fields(SETTINGS) == {
        "game_type": "GOMOKU",
        "board_size": 19,
        "win_length": 5,
        "starting_color": "WHITE",
    }


def test_joined_carries_the_configuration() -> None:
    assert joined("abc123", "Friendly Match", Color.BLACK, SETTINGS) == {
        "type": "joined",
        "room_id": "abc123",
        "room_name": "Friendly Match",
        "your_color": "BLACK",
        "game_type": "GOMOKU",
        "board_size": 19,
        "win_length": 5,
        "starting_color": "WHITE",
    }


def test_game_start_carries_the_configuration() -> None:
    assert game_start(Color.WHITE, SETTINGS) == {
        "type": "game_start",
        "game_type": "GOMOKU",
        "board_size": 19,
        "win_length": 5,
        "starting_color": "WHITE",
        "current_turn": "WHITE",
    }


def test_restart_is_per_player() -> None:
    assert restart(Color.WHITE, Color.WHITE, SETTINGS) == {
        "type": "restart",
        "your_color": "WHITE",
        "game_type": "GOMOKU",
        "board_size": 19,
        "win_length": 5,
        "starting_color": "WHITE",
        "current_turn": "WHITE",
    }
    assert restart(Color.BLACK, Color.WHITE, SETTINGS)["your_color"] == "BLACK"


def test_simple_messages() -> None:
    assert player_joined(Color.BLACK) == {"type": "player_joined", "color": "BLACK"}
    assert player_disconnected(Color.WHITE) == {
        "type": "player_disconnected",
        "color": "WHITE",
    }
    assert restart_requested(Color.WHITE, 1, 2) == {
        "type": "restart_requested",
        "color": "WHITE",
        "requested": 1,
        "required": 2,
    }
    assert pong() == {"type": "pong"}
    assert error(ErrorCode.ROOM_FULL, "full") == {
        "type": "error",
        "code": "ROOM_FULL",
        "message": "full",
    }

# ----------------------------------------------------------------------
# move results and game over
# ----------------------------------------------------------------------
def test_move_result() -> None:
    game = GomokuGame()
    result = game.make_move(Color.WHITE, 7, 7)

    assert move_result(result) == {
        "type": "move_result",
        "game_type": "GOMOKU",
        "x": 7,
        "y": 7,
        "color": "WHITE",
        "next_turn": "BLACK",
    }


def test_move_result_has_no_next_turn_after_the_game_over() -> None:
    game = finished_game([(3, 7), (4, 7), (5, 7), (6, 7), (7, 7)])
    result = MoveResult(
        x=7,
        y=7,
        color=Color.WHITE,
        next_turn=game.current_turn,
        status=game.status,
        winner=game.winner,
        loser=game.loser,
        reason=game.reason,
    )

    assert move_result(result)["next_turn"] is None


def test_game_over_for_a_win() -> None:
    result = MoveResult(
        x=7,
        y=7,
        color=Color.WHITE,
        next_turn=None,
        status=GameStatus.FINISHED,
        winner=Color.WHITE,
        loser=Color.BLACK,
        reason=GameOverReason.FIVE_IN_A_ROW,
    )

    assert game_over(result) == {
        "type": "game_over",
        "game_type": "GOMOKU",
        "winner": "WHITE",
        "loser": "BLACK",
        "reason": "five_in_a_row",
        "message": "WHITE wins.",
    }


def test_game_over_when_the_constrained_player_is_stuck() -> None:
    result = MoveResult(
        x=8,
        y=9,
        color=Color.BLACK,
        next_turn=None,
        status=GameStatus.FINISHED,
        winner=Color.BLACK,
        loser=Color.WHITE,
        reason=GameOverReason.NO_FORBIDDEN_FREE_MOVE,
    )

    assert game_over(result) == {
        "type": "game_over",
        "game_type": "GOMOKU",
        "winner": "BLACK",
        "loser": "WHITE",
        "reason": "no_forbidden_free_move",
        "message": "WHITE has no playable point left.",
    }


def test_game_over_never_mentions_a_forbidden_move() -> None:
    """Forbidden points are blocked, so they can never end a game."""
    assert not hasattr(GameOverReason, "FORBIDDEN_MOVE")
    assert "forbidden_move" not in {reason.value for reason in GameOverReason}


def test_game_over_for_a_draw() -> None:
    result = MoveResult(
        x=3,
        y=3,
        color=Color.BLACK,
        next_turn=None,
        status=GameStatus.FINISHED,
        reason=GameOverReason.DRAW,
    )

    assert game_over(result) == {
        "type": "game_over",
        "game_type": "GOMOKU",
        "winner": None,
        "loser": None,
        "reason": "draw",
        "message": "Draw.",
    }


def test_othello_game_over_includes_score() -> None:
    result = MoveResult(
        x=7,
        y=7,
        color=Color.BLACK,
        next_turn=None,
        status=GameStatus.FINISHED,
        winner=Color.BLACK,
        loser=Color.WHITE,
        reason=GameOverReason.BOARD_FULL,
        score={Color.BLACK: 35, Color.WHITE: 29},
        game_type=GameType.OTHELLO,
    )

    assert game_over(result) == {
        "type": "game_over",
        "game_type": "OTHELLO",
        "winner": "BLACK",
        "loser": "WHITE",
        "reason": "board_full",
        "score": {"BLACK": 35, "WHITE": 29},
        "message": "BLACK wins 35 to 29.",
    }


def test_describe_a_stuck_constrained_player() -> None:
    text = describe_game_over(
        Color.BLACK, Color.WHITE, GameOverReason.NO_FORBIDDEN_FREE_MOVE
    )

    assert text == "WHITE has no playable point left."


def test_describe_other_results() -> None:
    assert (
        describe_game_over(Color.BLACK, Color.WHITE, GameOverReason.FIVE_IN_A_ROW)
        == "BLACK wins."
    )
    assert describe_game_over(None, None, GameOverReason.DRAW) == "Draw."
    assert describe_game_over(None, None, None) == "Game over."


# ----------------------------------------------------------------------
# forbidden point rejection
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "kind,expected",
    [
        (ForbiddenType.DOUBLE_THREE, "Double-three is forbidden for WHITE."),
        (ForbiddenType.DOUBLE_FOUR, "Double-four is forbidden for WHITE."),
        (ForbiddenType.OVERLINE, "Overline is forbidden for WHITE."),
    ],
)
def test_forbidden_move_error_payload(
    kind: ForbiddenType, expected: str
) -> None:
    exc = ForbiddenMoveError(kind, 7, 9)

    assert forbidden_move_error(Color.WHITE, exc) == {
        "type": "error",
        "code": "FORBIDDEN_MOVE",
        "message": expected,
        "forbidden_type": kind.value,
        "x": 7,
        "y": 9,
    }


def test_forbidden_moves_are_ordered_by_row_then_column() -> None:
    points = {
        (9, 1): ForbiddenType.OVERLINE,
        (2, 3): ForbiddenType.DOUBLE_FOUR,
        (1, 1): ForbiddenType.DOUBLE_THREE,
    }

    assert forbidden_moves(points) == [
        {"x": 1, "y": 1, "forbidden_type": "DOUBLE_THREE"},
        {"x": 9, "y": 1, "forbidden_type": "OVERLINE"},
        {"x": 2, "y": 3, "forbidden_type": "DOUBLE_FOUR"},
    ]


def test_forbidden_moves_of_an_empty_position() -> None:
    assert forbidden_moves({}) == []


# ----------------------------------------------------------------------
# game_state
# ----------------------------------------------------------------------
def test_game_state_while_playing() -> None:
    game = GomokuGame()
    game.make_move(Color.WHITE, 7, 7)

    state = game_state(game)

    assert state["type"] == "game_state"
    assert state["status"] == "PLAYING"
    assert state["current_turn"] == "BLACK"
    assert state["starting_color"] == "WHITE"
    assert state["winner"] is None
    assert state["loser"] is None
    assert state["game_over_reason"] is None
    assert state["board"][7][7] == "WHITE"
    assert state["last_move"] == {"x": 7, "y": 7}
    assert state["constrained_color"] == "WHITE"
    assert state["forbidden_moves"] == []
    assert "forbidden_type" not in state


def test_game_state_after_a_win_keeps_the_result() -> None:
    game = finished_game([(3, 7), (4, 7), (5, 7), (6, 7), (7, 7)])

    state = game_state(game)

    assert state["status"] == "FINISHED"
    assert state["winner"] == "WHITE"
    assert state["loser"] == "BLACK"
    assert state["game_over_reason"] == "five_in_a_row"
    # No turn belongs to a finished game.
    assert state["current_turn"] is None


def test_game_state_carries_the_forbidden_points() -> None:
    """The constrained player's blocked points travel with every snapshot."""
    game = GomokuGame()
    for index, (x, y) in enumerate([(5, 7), (6, 7), (7, 5), (7, 6)]):
        game.make_move(Color.WHITE, x, y)
        game.make_move(Color.BLACK, *FILLERS[index])

    state = game_state(game)

    assert state["constrained_color"] == "WHITE"
    assert {"x": 7, "y": 7, "forbidden_type": "DOUBLE_THREE"} in (
        state["forbidden_moves"]
    )
    assert state["current_turn"] == "WHITE"
    # A blocked point is empty, by definition.
    assert state["board"][7][7] is None


def test_game_state_of_a_finished_game_has_no_forbidden_points() -> None:
    game = finished_game([(3, 7), (4, 7), (5, 7), (6, 7), (7, 7)])

    state = game_state(game)

    assert state["status"] == "FINISHED"
    assert state["forbidden_moves"] == []
    assert state["current_turn"] is None


def test_othello_game_state_has_no_forbidden_fields() -> None:
    """Othello knows no forbidden points, the way Gomoku knows no score."""
    game = OthelloGame()
    game.make_move(Color.BLACK, 2, 3)

    state = game_state(game)

    assert "constrained_color" not in state
    assert "forbidden_moves" not in state
    assert state["score"] == {"BLACK": 4, "WHITE": 1}
    assert state["last_move"] == {"x": 2, "y": 3}


def test_game_state_of_an_untouched_board_has_no_last_move() -> None:
    assert game_state(GomokuGame())["last_move"] is None


def test_game_state_is_json_serialisable() -> None:
    game = finished_game([(3, 7), (4, 7), (5, 7), (6, 7), (7, 7)])

    encoded = json.dumps(game_state(game))

    assert json.loads(encoded)["winner"] == "WHITE"


# ----------------------------------------------------------------------
# lobby messages
# ----------------------------------------------------------------------
def test_parse_lobby_commands() -> None:
    assert parse_client_message('{"type": "get_room_list"}') == GetRoomListCommand()
    assert parse_client_message(
        '{"type": "create_room", "room_name": "  Ｇａｍｅ 방  "}'
    ) == CreateRoomCommand(room_name="Game 방")
    assert parse_client_message('{"type": "leave_room"}') == LeaveRoomCommand()
    assert parse_client_message(
        '{"type": "join_room", "room_id": "room_001"}'
    ) == JoinRoomCommand(room_id="room_001")


def test_parse_create_room_ignores_client_settings() -> None:
    raw = json.dumps(
        {
            "type": "create_room",
            "room_name": "Friendly Match",
            "room_id": "client-id",
            "board_size": 19,
            "win_length": 4,
        }
    )

    assert parse_client_message(raw) == CreateRoomCommand(
        room_name="Friendly Match"
    )


def test_parse_create_room_accepts_othello() -> None:
    assert parse_client_message(
        '{"type":"create_room","room_name":"Othello","game_type":"OTHELLO"}'
    ) == CreateRoomCommand(room_name="Othello", game_type=GameType.OTHELLO)


@pytest.mark.parametrize("game_type", ["CHESS", 7, None])
def test_parse_create_room_rejects_invalid_game_type(game_type: object) -> None:
    with pytest.raises(ProtocolError) as excinfo:
        parse_client_message(json.dumps({
            "type": "create_room", "room_name": "Invalid", "game_type": game_type
        }))
    assert excinfo.value.code is ErrorCode.INVALID_GAME_TYPE


@pytest.mark.parametrize(
    "room_name",
    [None, 7, "", "   ", "가" * 31, "first\nsecond", "bad\x00name", "\ud800"],
)
def test_create_room_rejects_invalid_room_names(room_name: object) -> None:
    raw = json.dumps({"type": "create_room", "room_name": room_name})

    with pytest.raises(ProtocolError) as excinfo:
        parse_client_message(raw)

    assert excinfo.value.code is ErrorCode.INVALID_ROOM_NAME


def test_parse_join_room_trims_the_room_id() -> None:
    raw = json.dumps({"type": "join_room", "room_id": "  room_002  "})

    assert parse_client_message(raw) == JoinRoomCommand(room_id="room_002")


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "join_room"},
        {"type": "join_room", "room_id": ""},
        {"type": "join_room", "room_id": "   "},
        {"type": "join_room", "room_id": 7},
        {"type": "join_room", "room_id": None},
    ],
)
def test_join_room_needs_a_room_id(payload: dict[str, object]) -> None:
    with pytest.raises(ProtocolError) as excinfo:
        parse_client_message(json.dumps(payload))

    assert excinfo.value.code is ErrorCode.INVALID_MESSAGE


def test_connected_message() -> None:
    assert connected(SETTINGS) == {
        "type": "connected",
        "supported_game_types": ["GOMOKU", "OTHELLO"],
        "game_type": "GOMOKU",
        "board_size": 19,
        "win_length": 5,
        "starting_color": "WHITE",
    }


def test_room_created_and_left_room() -> None:
    assert room_created("room_001", "Friendly Match") == {
        "type": "room_created",
        "room_id": "room_001",
        "room_name": "Friendly Match",
        "game_type": "GOMOKU",
    }
    assert left_room("room_001") == {"type": "left_room", "room_id": "room_001"}


def test_room_list_message() -> None:
    summaries = [
        RoomSummary(
            room_id="room_001",
            room_name="First",
            board_size=19,
            win_length=5,
            players=1,
            max_players=2,
            status=GameStatus.WAITING,
        ),
        RoomSummary(
            room_id="room_002",
            room_name="Second",
            board_size=15,
            win_length=5,
            players=2,
            max_players=2,
            status=GameStatus.PLAYING,
        ),
    ]

    assert room_list(summaries) == {
        "type": "room_list",
        "rooms": [
            {
                "room_id": "room_001",
                "room_name": "First",
                "game_type": "GOMOKU",
                "board_size": 19,
                "win_length": 5,
                "players": 1,
                "max_players": 2,
                "status": "WAITING",
            },
            {
                "room_id": "room_002",
                "room_name": "Second",
                "game_type": "GOMOKU",
                "board_size": 15,
                "win_length": 5,
                "players": 2,
                "max_players": 2,
                "status": "PLAYING",
            },
        ],
    }


def test_empty_room_list_message() -> None:
    assert room_list([]) == {"type": "room_list", "rooms": []}


def test_room_list_is_json_serialisable() -> None:
    room = GameRoom.from_settings("room_001", GameSettings(board_size=19))

    encoded = json.dumps(room_list([room.summary()]))

    assert json.loads(encoded)["rooms"][0]["room_id"] == "room_001"
