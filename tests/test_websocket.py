"""WebSocket integration tests driving the real FastAPI application.

Clients connect to ``/ws`` and land in the lobby; rooms are created and
entered with explicit messages.  Game rules: WHITE opens and is bound by the
Renju forbidden moves, the loser of a round opens the next one.
"""

from __future__ import annotations

from contextlib import contextmanager
from itertools import count
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import (
    DEFAULT_BOARD_SIZE,
    DEFAULT_STARTING_COLOR,
    DEFAULT_WIN_LENGTH,
    RULE_NAME,
    ServerConfig,
)
from app.main import create_app

FIRST_ROOM_ID = "room_001"
ROOM_NAME_SEQUENCE = count(1)

#: Harmless answers of the non-building color.
FILLERS: list[tuple[int, int]] = [(0, 0), (14, 0), (0, 14), (14, 14)]

#: WHITE builds five in a row on row 7.
WHITE_WIN_MOVES: list[tuple[int, int]] = [(3, 7), (4, 7), (5, 7), (6, 7), (7, 7)]

#: Builds the position in front of the double-three point ``(7, 7)``.
#: After playing these, it is WHITE to move and ``(7, 7)`` is forbidden.
WHITE_DOUBLE_THREE_SETUP: list[tuple[int, int]] = [(5, 7), (6, 7), (7, 5), (7, 6)]

#: The point ``WHITE_DOUBLE_THREE_SETUP`` makes unplayable for WHITE.
WHITE_FORBIDDEN_POINT: tuple[int, int] = (7, 7)


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client19() -> Iterator[TestClient]:
    with TestClient(create_app(ServerConfig(board_size=19))) as test_client:
        yield test_client


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def expect(ws: Any, message_type: str) -> dict[str, Any]:
    """Receive the next message and assert on its ``type``."""
    message = ws.receive_json()
    assert message["type"] == message_type, message
    return message


def expect_move(ws: Any) -> dict[str, Any]:
    """Consume one accepted move: ``move_result`` and the snapshot after it.

    Every accepted move is followed by an authoritative ``game_state`` (the
    Gomoku forbidden points and the Othello flips both live there), so tests
    that keep playing have to drain both frames.
    """
    move = expect(ws, "move_result")
    expect(ws, "game_state")
    return move


def assert_settings(message: dict[str, Any], board_size: int) -> None:
    """Every configuration carrying message repeats the room setup."""
    assert message["board_size"] == board_size, message
    assert message["win_length"] == DEFAULT_WIN_LENGTH, message
    assert message["starting_color"] == DEFAULT_STARTING_COLOR.value, message


def lobby_handshake(
    ws: Any, board_size: int = DEFAULT_BOARD_SIZE
) -> list[dict[str, Any]]:
    """Consume ``connected`` + ``room_list`` and return the room list."""
    connected = expect(ws, "connected")
    assert_settings(connected, board_size)
    return expect(ws, "room_list")["rooms"]


def create_room(
    ws: Any,
    board_size: int = DEFAULT_BOARD_SIZE,
    room_name: str | None = None,
) -> str:
    """Create a room; the creator is seated as WHITE right away."""
    expected_name = room_name or f"Test Room {next(ROOM_NAME_SEQUENCE)}"
    ws.send_json({"type": "create_room", "room_name": expected_name})
    created = expect(ws, "room_created")
    room_id = created["room_id"]
    assert created["room_name"] == expected_name

    joined = expect(ws, "joined")
    assert joined["room_id"] == room_id
    assert joined["room_name"] == expected_name
    assert joined["your_color"] == "WHITE"
    assert_settings(joined, board_size)

    state = expect(ws, "game_state")
    assert state["status"] == "WAITING"
    assert len(state["board"]) == board_size
    return room_id


def join_room(
    first: Any, second: Any, room_id: str, board_size: int = DEFAULT_BOARD_SIZE
) -> None:
    """Second client joins ``room_id``; the game starts for both."""
    second.send_json({"type": "join_room", "room_id": room_id})

    joined = expect(second, "joined")
    assert joined["room_id"] == room_id
    assert isinstance(joined["room_name"], str) and joined["room_name"]
    assert joined["your_color"] == "BLACK"
    assert_settings(joined, board_size)

    assert expect(first, "player_joined")["color"] == "BLACK"
    for ws in (first, second):
        start = expect(ws, "game_start")
        assert start["current_turn"] == "WHITE"
        assert_settings(start, board_size)
        state = expect(ws, "game_state")
        assert state["status"] == "PLAYING"
        assert_settings(state, board_size)


@contextmanager
def two_players(
    client: TestClient, board_size: int = DEFAULT_BOARD_SIZE
) -> Iterator[tuple[Any, Any, str]]:
    """Yield (WHITE, BLACK, room_id) for a freshly started round."""
    with client.websocket_connect("/ws") as first:
        lobby_handshake(first, board_size)
        room_id = create_room(first, board_size)
        with client.websocket_connect("/ws") as second:
            lobby_handshake(second, board_size)
            join_room(first, second, room_id, board_size)
            yield first, second, room_id


def play_sequence(
    builder: Any,
    answerer: Any,
    moves: list[tuple[int, int]],
    fillers: list[tuple[int, int]] | None = None,
) -> None:
    """``builder`` plays ``moves``; the other side answers with fillers."""
    filler_moves = fillers if fillers is not None else FILLERS
    last = len(moves) - 1
    for index, (x, y) in enumerate(moves):
        builder.send_json({"type": "move", "x": x, "y": y})
        for ws in (builder, answerer):
            expect_move(ws)
        if index == last:
            return
        fx, fy = filler_moves[index]
        answerer.send_json({"type": "move", "x": fx, "y": fy})
        for ws in (builder, answerer):
            expect_move(ws)


def build_forbidden_position(white: Any, black: Any) -> dict[str, Any]:
    """Play up to the double-three point, leaving WHITE to move.

    Returns the last ``game_state`` WHITE received, which is the snapshot
    that advertises the forbidden points.
    """
    state: dict[str, Any] = {}
    for index, (x, y) in enumerate(WHITE_DOUBLE_THREE_SETUP):
        white.send_json({"type": "move", "x": x, "y": y})
        expect(white, "move_result")
        state = expect(white, "game_state")
        expect_move(black)

        fx, fy = FILLERS[index]
        black.send_json({"type": "move", "x": fx, "y": fy})
        expect(white, "move_result")
        state = expect(white, "game_state")
        expect_move(black)
    return state


# ----------------------------------------------------------------------
# HTTP surface
# ----------------------------------------------------------------------
def test_index_and_health(client: TestClient) -> None:
    index = client.get("/").json()
    assert index["board_size"] == DEFAULT_BOARD_SIZE
    assert index["win_length"] == DEFAULT_WIN_LENGTH
    assert index["rule"] == RULE_NAME
    assert index["starting_color"] == DEFAULT_STARTING_COLOR.value
    assert index["max_players"] == 2
    assert index["websocket"] == "/ws"

    assert client.get("/health").json() == {
        "status": "ok",
        "board_size": DEFAULT_BOARD_SIZE,
        "rooms": 0,
        "connections": 0,
        "lobby": 0,
        "players": 0,
    }


# ----------------------------------------------------------------------
# lobby
# ----------------------------------------------------------------------
def test_connect_lands_in_the_lobby(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        connected = expect(ws, "connected")
        assert connected["board_size"] == DEFAULT_BOARD_SIZE
        assert connected["starting_color"] == "WHITE"

        assert expect(ws, "room_list")["rooms"] == []


def test_ping_works_in_the_lobby(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        lobby_handshake(ws)
        ws.send_json({"type": "ping"})

        assert ws.receive_json() == {"type": "pong"}


def test_get_room_list_on_demand(client: TestClient) -> None:
    with client.websocket_connect("/ws") as first:
        lobby_handshake(first)
        room_id = create_room(first)

        with client.websocket_connect("/ws") as watcher:
            rooms = lobby_handshake(watcher)
            assert [room["room_id"] for room in rooms] == [room_id]

            watcher.send_json({"type": "get_room_list"})
            rooms = expect(watcher, "room_list")["rooms"]

            assert rooms == [
                {
                    "room_id": room_id,
                    "room_name": rooms[0]["room_name"],
                    "game_type": "GOMOKU",
                    "board_size": DEFAULT_BOARD_SIZE,
                    "win_length": DEFAULT_WIN_LENGTH,
                    "players": 1,
                    "max_players": 2,
                    "status": "WAITING",
                }
            ]


def test_create_room_seats_the_creator_as_white(
    client: TestClient, app: FastAPI
) -> None:
    with client.websocket_connect("/ws") as ws:
        lobby_handshake(ws)

        room_id = create_room(ws)

        assert room_id == FIRST_ROOM_ID
        room = app.state.rooms.get_room(room_id)
        assert room is not None
        assert room.player_count == 1
        assert client.get("/health").json()["rooms"] == 1


def test_room_ids_come_from_the_server(client: TestClient) -> None:
    with client.websocket_connect("/ws") as first:
        lobby_handshake(first)
        assert create_room(first) == "room_001"

        with client.websocket_connect("/ws") as second:
            lobby_handshake(second)
            assert create_room(second) == "room_002"


def test_room_list_is_broadcast_to_lobby_clients(client: TestClient) -> None:
    with client.websocket_connect("/ws") as watcher:
        assert lobby_handshake(watcher) == []

        with client.websocket_connect("/ws") as owner:
            lobby_handshake(owner)
            room_id = create_room(owner)

            # The watcher is still in the lobby and gets the update pushed.
            rooms = expect(watcher, "room_list")["rooms"]
            assert len(rooms) == 1
            assert rooms[0]["room_id"] == room_id
            assert rooms[0]["players"] == 1
            assert rooms[0]["status"] == "WAITING"

            with client.websocket_connect("/ws") as joiner:
                lobby_handshake(joiner)
                join_room(owner, joiner, room_id)

                rooms = expect(watcher, "room_list")["rooms"]
                assert rooms[0]["players"] == 2
                assert rooms[0]["status"] == "PLAYING"


def test_joining_an_unknown_room_keeps_the_client_in_the_lobby(
    client: TestClient
) -> None:
    with client.websocket_connect("/ws") as ws:
        lobby_handshake(ws)

        ws.send_json({"type": "join_room", "room_id": "room_999"})
        error = expect(ws, "error")
        assert error["code"] == "ROOM_NOT_FOUND"

        # Still connected and still in the lobby.
        ws.send_json({"type": "get_room_list"})
        assert expect(ws, "room_list")["rooms"] == []
        assert client.get("/health").json()["rooms"] == 0


def test_joining_a_full_room_is_rejected_without_closing_the_socket(
    client: TestClient
) -> None:
    with two_players(client) as (_, _, room_id):
        with client.websocket_connect("/ws") as third:
            lobby_handshake(third)

            third.send_json({"type": "join_room", "room_id": room_id})
            assert expect(third, "error")["code"] == "ROOM_FULL"

            # The socket stays usable and the client stays in the lobby.
            third.send_json({"type": "ping"})
            expect(third, "pong")


def test_joining_a_second_room_is_rejected(client: TestClient) -> None:
    with client.websocket_connect("/ws") as owner:
        lobby_handshake(owner)
        create_room(owner)

        with client.websocket_connect("/ws") as other:
            lobby_handshake(other)
            other_room = create_room(other)

            owner.send_json({"type": "join_room", "room_id": other_room})
            assert expect(owner, "error")["code"] == "ALREADY_IN_ROOM"


def test_creating_a_second_room_is_rejected(
    client: TestClient, app: FastAPI
) -> None:
    with client.websocket_connect("/ws") as ws:
        lobby_handshake(ws)
        create_room(ws)

        ws.send_json({"type": "create_room", "room_name": "Another Room"})
        assert expect(ws, "error")["code"] == "ALREADY_IN_ROOM"

        assert app.state.rooms.get_room_count() == 1


def test_client_supplied_board_size_is_ignored_on_create(
    client: TestClient, app: FastAPI
) -> None:
    with client.websocket_connect("/ws") as ws:
        lobby_handshake(ws)
        ws.send_json(
            {
                "type": "create_room",
                "room_name": "Server Settings",
                "board_size": 19,
                "room_id": "hack",
            }
        )

        room_id = expect(ws, "room_created")["room_id"]
        joined = expect(ws, "joined")
        expect(ws, "game_state")

        assert room_id == FIRST_ROOM_ID
        assert joined["board_size"] == DEFAULT_BOARD_SIZE
        assert app.state.rooms.settings.board_size == DEFAULT_BOARD_SIZE


def test_room_name_is_normalized_and_sent_in_every_room_message(
    client: TestClient,
) -> None:
    with client.websocket_connect("/ws") as owner:
        lobby_handshake(owner)
        owner.send_json({"type": "create_room", "room_name": "  Ｇａｍｅ 방  "})

        created = expect(owner, "room_created")
        assert created["room_name"] == "Game 방"
        joined = expect(owner, "joined")
        assert joined["room_name"] == "Game 방"
        expect(owner, "game_state")

        with client.websocket_connect("/ws") as watcher:
            rooms = lobby_handshake(watcher)
            assert rooms[0]["room_name"] == "Game 방"


@pytest.mark.parametrize(
    "room_name",
    [None, 7, "", "   ", "가" * 31, "first\nsecond", "bad\x00name", "\ud800"],
)
def test_invalid_room_name_keeps_client_in_lobby(
    client: TestClient, room_name: object
) -> None:
    with client.websocket_connect("/ws") as ws:
        lobby_handshake(ws)
        ws.send_json({"type": "create_room", "room_name": room_name})

        assert expect(ws, "error")["code"] == "INVALID_ROOM_NAME"
        ws.send_json({"type": "get_room_list"})
        assert expect(ws, "room_list")["rooms"] == []


def test_duplicate_room_name_is_rejected_by_nfkc_casefold(
    client: TestClient, app: FastAPI
) -> None:
    with client.websocket_connect("/ws") as owner:
        lobby_handshake(owner)
        create_room(owner, room_name="Game 방")

        with client.websocket_connect("/ws") as challenger:
            lobby_handshake(challenger)
            challenger.send_json(
                {"type": "create_room", "room_name": "ＧＡＭＥ 방"}
            )

            error = expect(challenger, "error")
            assert error["code"] == "ROOM_NAME_TAKEN"
            assert error["message"] == "A room with that name already exists."
            assert app.state.rooms.get_room_count() == 1

            challenger.send_json({"type": "ping"})
            expect(challenger, "pong")


def test_unexpected_create_failure_is_sanitized(
    client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_create_room(
        connection_id: str, room_name: object
    ) -> object:
        raise RuntimeError("internal detail that must not reach the client")

    monkeypatch.setattr(app.state.rooms, "create_room", fail_create_room)

    with client.websocket_connect("/ws") as ws:
        lobby_handshake(ws)
        ws.send_json({"type": "create_room", "room_name": "Friendly Match"})

        error = expect(ws, "error")
        assert error == {
            "type": "error",
            "code": "CREATE_ROOM_FAILED",
            "message": "The room could not be created.",
        }
        assert app.state.rooms.get_room_count() == 0

        ws.send_json({"type": "ping"})
        expect(ws, "pong")


def test_game_messages_are_rejected_in_the_lobby(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        lobby_handshake(ws)

        ws.send_json({"type": "move", "x": 7, "y": 7})
        assert expect(ws, "error")["code"] == "NOT_IN_ROOM"

        ws.send_json({"type": "restart_request"})
        assert expect(ws, "error")["code"] == "NOT_IN_ROOM"

        ws.send_json({"type": "leave_room"})
        assert expect(ws, "error")["code"] == "NOT_IN_ROOM"


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "join_room"},
        {"type": "join_room", "room_id": ""},
        {"type": "join_room", "room_id": 1},
        {"type": "join_room", "room_id": None},
    ],
)
def test_join_room_needs_a_room_id(client: TestClient, payload: Any) -> None:
    with client.websocket_connect("/ws") as ws:
        lobby_handshake(ws)
        ws.send_json(payload)

        assert expect(ws, "error")["code"] == "INVALID_MESSAGE"


# ----------------------------------------------------------------------
# moves
# ----------------------------------------------------------------------
def test_moves_are_broadcast_to_both_players(client: TestClient) -> None:
    with two_players(client) as (white, black, _):
        white.send_json({"type": "move", "x": 7, "y": 7})
        for ws in (white, black):
            assert ws.receive_json() == {
                "type": "move_result",
                "game_type": "GOMOKU",
                "x": 7,
                "y": 7,
                "color": "WHITE",
                "next_turn": "BLACK",
            }
            state = expect(ws, "game_state")
            assert state["board"][7][7] == "WHITE"
            assert state["last_move"] == {"x": 7, "y": 7}
            assert state["constrained_color"] == "WHITE"
            assert state["forbidden_moves"] == []

        black.send_json({"type": "move", "x": 7, "y": 8})
        for ws in (white, black):
            assert ws.receive_json() == {
                "type": "move_result",
                "game_type": "GOMOKU",
                "x": 7,
                "y": 8,
                "color": "BLACK",
                "next_turn": "WHITE",
            }
            expect(ws, "game_state")


def test_othello_room_starts_and_broadcasts_flips(client: TestClient) -> None:
    with client.websocket_connect("/ws") as black:
        lobby_handshake(black)
        black.send_json({
            "type": "create_room",
            "room_name": "Othello Test",
            "game_type": "OTHELLO",
        })
        created = expect(black, "room_created")
        assert created["game_type"] == "OTHELLO"
        room_id = created["room_id"]
        joined = expect(black, "joined")
        assert joined["your_color"] == "BLACK"
        assert joined["board_size"] == 8
        assert joined["win_length"] is None
        assert joined["starting_color"] == "BLACK"
        waiting = expect(black, "game_state")
        assert waiting["score"] == {"BLACK": 2, "WHITE": 2}

        with client.websocket_connect("/ws") as white:
            rooms = lobby_handshake(white)
            summary = next(room for room in rooms if room["room_id"] == room_id)
            assert summary["game_type"] == "OTHELLO"
            assert summary["board_size"] == 8
            assert summary["win_length"] is None

            white.send_json({"type": "join_room", "room_id": room_id})
            assert expect(white, "joined")["your_color"] == "WHITE"
            assert expect(black, "player_joined")["color"] == "WHITE"
            for ws in (black, white):
                assert expect(ws, "game_start")["current_turn"] == "BLACK"
                state = expect(ws, "game_state")
                assert {tuple(move.values()) for move in state["legal_moves"]} == {
                    (2, 3), (3, 2), (4, 5), (5, 4)
                }

            black.send_json({"type": "move", "x": 2, "y": 3})
            for ws in (black, white):
                move = expect(ws, "move_result")
                assert move == {
                    "type": "move_result",
                    "game_type": "OTHELLO",
                    "x": 2,
                    "y": 3,
                    "color": "BLACK",
                    "next_turn": "WHITE",
                    "flipped": [{"x": 3, "y": 3, "color": "BLACK"}],
                    "passed_color": None,
                }
                state = expect(ws, "game_state")
                assert state["board"][3][3] == "BLACK"
                assert state["score"] == {"BLACK": 4, "WHITE": 1}


def test_black_cannot_open_the_game(client: TestClient) -> None:
    with two_players(client) as (white, black, _):
        black.send_json({"type": "move", "x": 7, "y": 7})

        assert expect(black, "error")["code"] == "NOT_YOUR_TURN"

        # WHITE must not have seen a move_result: its next message is the pong.
        white.send_json({"type": "ping"})
        expect(white, "pong")


def test_occupied_position_is_rejected(client: TestClient) -> None:
    with two_players(client) as (white, black, _):
        white.send_json({"type": "move", "x": 7, "y": 7})
        for ws in (white, black):
            expect_move(ws)

        black.send_json({"type": "move", "x": 7, "y": 7})
        assert expect(black, "error")["code"] == "POSITION_OCCUPIED"

        black.send_json({"type": "move", "x": 8, "y": 8})
        for ws in (white, black):
            expect_move(ws)


@pytest.mark.parametrize("x,y", [(15, 7), (7, 15), (-1, 0), (0, -1)])
def test_out_of_range_move_is_rejected(client: TestClient, x: int, y: int) -> None:
    with two_players(client) as (white, _, _):
        white.send_json({"type": "move", "x": x, "y": y})
        assert expect(white, "error")["code"] == "OUT_OF_RANGE"


def test_move_before_the_second_player_joins_is_rejected(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        lobby_handshake(ws)
        create_room(ws)

        ws.send_json({"type": "move", "x": 7, "y": 7})
        assert expect(ws, "error")["code"] == "GAME_NOT_STARTED"


def test_client_supplied_state_is_ignored(client: TestClient) -> None:
    with two_players(client) as (white, black, _):
        black.send_json(
            {"type": "move", "x": 7, "y": 7, "color": "WHITE", "current_turn": "BLACK"}
        )
        assert expect(black, "error")["code"] == "NOT_YOUR_TURN"

        white.send_json({"type": "move", "x": 7, "y": 7, "color": "BLACK"})
        for ws in (white, black):
            assert expect_move(ws)["color"] == "WHITE"


# ----------------------------------------------------------------------
# malformed input
# ----------------------------------------------------------------------
def test_invalid_json_does_not_kill_the_connection(client: TestClient) -> None:
    with two_players(client) as (white, _, _):
        white.send_text("not json at all")
        assert expect(white, "error")["code"] == "INVALID_MESSAGE"

        white.send_json({"type": "ping"})
        expect(white, "pong")


@pytest.mark.parametrize("payload", [[], "text", 42, {"no_type": 1}, {"type": 5}])
def test_non_conforming_messages_return_invalid_message(
    client: TestClient, payload: Any
) -> None:
    with two_players(client) as (white, _, _):
        white.send_json(payload)
        assert expect(white, "error")["code"] == "INVALID_MESSAGE"


def test_unknown_message_type(client: TestClient) -> None:
    with two_players(client) as (white, _, _):
        white.send_json({"type": "surrender"})
        assert expect(white, "error")["code"] == "UNKNOWN_MESSAGE_TYPE"


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "move"},
        {"type": "move", "x": "7", "y": 7},
        {"type": "move", "x": 7.5, "y": 7},
        {"type": "move", "x": True, "y": 7},
        {"type": "move", "x": None, "y": None},
    ],
)
def test_move_with_bad_coordinates(client: TestClient, payload: Any) -> None:
    with two_players(client) as (white, _, _):
        white.send_json(payload)
        assert expect(white, "error")["code"] == "INVALID_MOVE"


def test_binary_frames_are_rejected(client: TestClient) -> None:
    with two_players(client) as (white, _, _):
        white.send_bytes(b"\xff\xfe\x00")
        assert expect(white, "error")["code"] == "INVALID_MESSAGE"

        white.send_json({"type": "ping"})
        expect(white, "pong")


# ----------------------------------------------------------------------
# game over
# ----------------------------------------------------------------------
def test_win_is_announced_to_both_players(client: TestClient) -> None:
    with two_players(client) as (white, black, _):
        play_sequence(white, black, WHITE_WIN_MOVES)

        for ws in (white, black):
            assert ws.receive_json() == {
                "type": "game_over",
                "game_type": "GOMOKU",
                "winner": "WHITE",
                "loser": "BLACK",
                "reason": "five_in_a_row",
                "message": "WHITE wins.",
            }


def test_forbidden_points_are_advertised_to_both_players(
    client: TestClient,
) -> None:
    with two_players(client) as (white, black, _):
        state = build_forbidden_position(white, black)

        assert state["constrained_color"] == "WHITE"
        assert state["current_turn"] == "WHITE"
        assert {
            "x": 7,
            "y": 7,
            "forbidden_type": "DOUBLE_THREE",
        } in state["forbidden_moves"]


def test_a_forbidden_move_is_rejected_and_the_game_goes_on(
    client: TestClient,
) -> None:
    with two_players(client) as (white, black, _):
        build_forbidden_position(white, black)
        x, y = WHITE_FORBIDDEN_POINT

        white.send_json({"type": "move", "x": x, "y": y})

        assert white.receive_json() == {
            "type": "error",
            "code": "FORBIDDEN_MOVE",
            "message": "Double-three is forbidden for WHITE.",
            "forbidden_type": "DOUBLE_THREE",
            "x": x,
            "y": y,
        }

        # BLACK was told nothing at all: its next frame is the pong.
        black.send_json({"type": "ping"})
        expect(black, "pong")

        # And it is still WHITE to move.
        white.send_json({"type": "move", "x": 2, "y": 2})
        for ws in (white, black):
            assert expect_move(ws)["color"] == "WHITE"


def test_a_rejected_move_leaves_the_room_playing(client: TestClient) -> None:
    with client.websocket_connect("/ws") as watcher:
        lobby_handshake(watcher)

        with two_players(client) as (white, black, _):
            expect(watcher, "room_list")
            expect(watcher, "room_list")
            build_forbidden_position(white, black)
            x, y = WHITE_FORBIDDEN_POINT

            white.send_json({"type": "move", "x": x, "y": y})
            assert expect(white, "error")["code"] == "FORBIDDEN_MOVE"

            # No game_over, so the lobby never sees this room finish.
            watcher.send_json({"type": "ping"})
            expect(watcher, "pong")


def test_no_move_is_accepted_after_the_game_over(client: TestClient) -> None:
    with two_players(client) as (white, black, _):
        play_sequence(white, black, WHITE_WIN_MOVES)
        for ws in (white, black):
            expect(ws, "game_over")

        for ws in (white, black):
            ws.send_json({"type": "move", "x": 1, "y": 1})
            assert expect(ws, "error")["code"] == "GAME_ALREADY_FINISHED"


def test_finished_room_is_visible_in_the_lobby(client: TestClient) -> None:
    with client.websocket_connect("/ws") as watcher:
        lobby_handshake(watcher)

        with client.websocket_connect("/ws") as white:
            lobby_handshake(white)
            room_id = create_room(white)
            expect(watcher, "room_list")

            with client.websocket_connect("/ws") as black:
                lobby_handshake(black)
                join_room(white, black, room_id)
                expect(watcher, "room_list")

                play_sequence(white, black, WHITE_WIN_MOVES)
                for ws in (white, black):
                    expect(ws, "game_over")

                rooms = expect(watcher, "room_list")["rooms"]
                assert rooms[0]["status"] == "FINISHED"
                assert rooms[0]["players"] == 2


# ----------------------------------------------------------------------
# restart and color swap
# ----------------------------------------------------------------------
def test_loser_becomes_white_after_restart(client: TestClient) -> None:
    with two_players(client) as (white, black, _):
        play_sequence(white, black, WHITE_WIN_MOVES)
        for ws in (white, black):
            expect(ws, "game_over")

        white.send_json({"type": "restart_request"})
        for ws in (white, black):
            pending = expect(ws, "restart_requested")
            assert pending["color"] == "WHITE"
            assert pending["requested"] == 1
            assert pending["required"] == 2

        black.send_json({"type": "restart_request"})

        winner_restart = expect(white, "restart")
        assert winner_restart["your_color"] == "BLACK"
        assert winner_restart["current_turn"] == "WHITE"

        loser_restart = expect(black, "restart")
        assert loser_restart["your_color"] == "WHITE"

        for ws in (white, black):
            state = expect(ws, "game_state")
            assert state["status"] == "PLAYING"
            assert state["winner"] is None
            assert all(cell is None for row in state["board"] for cell in row)

        # The previous loser (now WHITE) opens the new round.
        black.send_json({"type": "move", "x": 7, "y": 7})
        for ws in (white, black):
            assert expect_move(ws)["color"] == "WHITE"


def test_colors_stay_when_white_loses(client: TestClient) -> None:
    with two_players(client) as (white, black, _):
        # BLACK wins, so the loser already holds the starting color.
        white.send_json({"type": "move", "x": 0, "y": 0})
        for ws in (white, black):
            expect_move(ws)
        play_sequence(
            black,
            white,
            [(3, 9), (4, 9), (5, 9), (6, 9), (7, 9)],
            fillers=[(14, 0), (0, 14), (14, 14), (14, 7)],
        )
        for ws in (white, black):
            assert expect(ws, "game_over")["loser"] == "WHITE"

        white.send_json({"type": "restart_request"})
        for ws in (white, black):
            expect(ws, "restart_requested")
        black.send_json({"type": "restart_request"})

        assert expect(white, "restart")["your_color"] == "WHITE"
        assert expect(black, "restart")["your_color"] == "BLACK"
        for ws in (white, black):
            expect(ws, "game_state")

        white.send_json({"type": "move", "x": 7, "y": 7})
        for ws in (white, black):
            assert expect_move(ws)["color"] == "WHITE"


def test_restart_is_refused_with_a_single_player(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        lobby_handshake(ws)
        create_room(ws)

        ws.send_json({"type": "restart_request"})
        assert expect(ws, "error")["code"] == "GAME_NOT_STARTED"


# ----------------------------------------------------------------------
# leaving and disconnecting
# ----------------------------------------------------------------------
def test_leave_room_returns_to_the_lobby(client: TestClient, app: FastAPI) -> None:
    with two_players(client) as (white, black, room_id):
        white.send_json({"type": "move", "x": 7, "y": 7})
        for ws in (white, black):
            expect_move(ws)

        black.send_json({"type": "leave_room"})

        assert expect(black, "left_room")["room_id"] == room_id
        rooms = expect(black, "room_list")["rooms"]
        assert rooms[0]["players"] == 1
        assert rooms[0]["status"] == "WAITING"

        # The remaining player is told and gets a cleared board.
        assert expect(white, "player_disconnected")["color"] == "BLACK"
        state = expect(white, "game_state")
        assert state["status"] == "WAITING"
        assert all(cell is None for row in state["board"] for cell in row)

        # The room survives with one player.
        room = app.state.rooms.get_room(room_id)
        assert room is not None
        assert room.player_count == 1

        # And the player that left may join again from the lobby.
        black.send_json({"type": "join_room", "room_id": room_id})
        assert expect(black, "joined")["your_color"] == "BLACK"


def test_leaving_last_removes_the_room(client: TestClient, app: FastAPI) -> None:
    with client.websocket_connect("/ws") as ws:
        lobby_handshake(ws)
        room_id = create_room(ws)

        ws.send_json({"type": "leave_room"})
        expect(ws, "left_room")
        assert expect(ws, "room_list")["rooms"] == []

        assert app.state.rooms.get_room(room_id) is None
        assert client.get("/health").json()["rooms"] == 0

        # The client is in the lobby and can create a new room.
        assert create_room(ws) == "room_002"


def test_disconnect_frees_the_seat_and_updates_the_lobby(
    client: TestClient, app: FastAPI
) -> None:
    with client.websocket_connect("/ws") as watcher:
        lobby_handshake(watcher)

        with client.websocket_connect("/ws") as white:
            lobby_handshake(white)
            room_id = create_room(white)
            expect(watcher, "room_list")

            with client.websocket_connect("/ws") as black:
                lobby_handshake(black)
                join_room(white, black, room_id)
                expect(watcher, "room_list")

            # BLACK is gone now.
            assert expect(white, "player_disconnected")["color"] == "BLACK"
            expect(white, "game_state")

            rooms = expect(watcher, "room_list")["rooms"]
            assert rooms[0]["players"] == 1
            assert rooms[0]["status"] == "WAITING"

            room = app.state.rooms.get_room(room_id)
            assert room is not None
            assert room.free_color() is not None

            white.send_json({"type": "move", "x": 7, "y": 7})
            assert expect(white, "error")["code"] == "GAME_NOT_STARTED"


def test_a_new_player_can_take_the_freed_seat(client: TestClient) -> None:
    with client.websocket_connect("/ws") as white:
        lobby_handshake(white)
        room_id = create_room(white)

        with client.websocket_connect("/ws") as black:
            lobby_handshake(black)
            join_room(white, black, room_id)
            white.send_json({"type": "move", "x": 7, "y": 7})
            for ws in (white, black):
                expect_move(ws)

        expect(white, "player_disconnected")
        expect(white, "game_state")

        with client.websocket_connect("/ws") as replacement:
            lobby_handshake(replacement)
            join_room(white, replacement, room_id)

        expect(white, "player_disconnected")
        expect(white, "game_state")


def test_room_is_removed_once_everybody_disconnects(
    client: TestClient, app: FastAPI
) -> None:
    with two_players(client) as (_, _, room_id):
        assert app.state.rooms.get_room(room_id) is not None

    assert app.state.rooms.get_room_count() == 0
    assert app.state.manager.get_connection_count() == 0
    assert client.get("/health").json() == {
        "status": "ok",
        "board_size": DEFAULT_BOARD_SIZE,
        "rooms": 0,
        "connections": 0,
        "lobby": 0,
        "players": 0,
    }


# ----------------------------------------------------------------------
# 19 x 19 servers
# ----------------------------------------------------------------------
def test_19x19_server_announces_its_geometry(client19: TestClient) -> None:
    index = client19.get("/").json()
    assert index["board_size"] == 19
    assert client19.get("/health").json()["board_size"] == 19

    with client19.websocket_connect("/ws") as ws:
        connected = expect(ws, "connected")
        assert connected["board_size"] == 19
        expect(ws, "room_list")

        room_id = create_room(ws, board_size=19)
        assert room_id == FIRST_ROOM_ID


def test_far_corner_is_playable_on_19x19(client19: TestClient) -> None:
    with two_players(client19, board_size=19) as (white, black, _):
        white.send_json({"type": "move", "x": 18, "y": 18})

        for ws in (white, black):
            assert ws.receive_json() == {
                "type": "move_result",
                "game_type": "GOMOKU",
                "x": 18,
                "y": 18,
                "color": "WHITE",
                "next_turn": "BLACK",
            }
            assert expect(ws, "game_state")["board_size"] == 19


def test_range_check_follows_the_room_size(
    client19: TestClient, client: TestClient
) -> None:
    """(15, 15) is legal on a 19x19 board and out of range on a 15x15 one."""
    with two_players(client19, board_size=19) as (white, _, _):
        white.send_json({"type": "move", "x": 15, "y": 15})
        assert expect_move(white)["x"] == 15

    with two_players(client) as (white, _, _):
        white.send_json({"type": "move", "x": 15, "y": 15})
        assert expect(white, "error")["code"] == "OUT_OF_RANGE"


def test_win_on_19x19_is_reported(client19: TestClient) -> None:
    with two_players(client19, board_size=19) as (white, black, _):
        play_sequence(
            white,
            black,
            [(14, 18), (15, 18), (16, 18), (17, 18), (18, 18)],
            fillers=[(0, 0), (2, 0), (4, 0), (6, 0)],
        )

        for ws in (white, black):
            over = ws.receive_json()
            assert over["type"] == "game_over"
            assert over["winner"] == "WHITE"
            assert over["loser"] == "BLACK"


def test_restart_keeps_the_19x19_board(client19: TestClient) -> None:
    with two_players(client19, board_size=19) as (white, black, _):
        white.send_json({"type": "move", "x": 18, "y": 18})
        for ws in (white, black):
            expect_move(ws)

        white.send_json({"type": "restart_request"})
        for ws in (white, black):
            expect(ws, "restart_requested")
        black.send_json({"type": "restart_request"})

        for ws in (white, black):
            restart = expect(ws, "restart")
            assert restart["board_size"] == 19
            assert restart["current_turn"] == "WHITE"

            state = expect(ws, "game_state")
            assert state["board_size"] == 19
            assert len(state["board"]) == 19
            assert all(cell is None for row in state["board"] for cell in row)
