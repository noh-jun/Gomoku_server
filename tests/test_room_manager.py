"""Tests for RoomManager: the server owned room list."""

from __future__ import annotations

import asyncio

import pytest

from app.board import Color
from app.config import GameSettings
from app.errors import (
    InvalidRoomNameError,
    RoomFullError,
    RoomNameTakenError,
    RoomNotFoundError,
)
from app.game import GameStatus
from app.game_type import GameType
from app.othello import OthelloGame
from app.room import MAX_PLAYERS, GameRoom
from app.room_manager import RoomManager

FILLERS: list[tuple[int, int]] = [(0, 0), (14, 0), (0, 14), (14, 14)]


async def full_room(manager: RoomManager) -> tuple[GameRoom, str, str]:
    """Create a room and seat two players in it."""
    room, first = await manager.create_room("conn-1", "Friendly Match")
    _, second = await manager.join_room(room.room_id, "conn-2")

    assert first.player.color is Color.WHITE
    assert second.player.color is Color.BLACK
    return room, "conn-1", "conn-2"


async def play_white_win(room: GameRoom) -> None:
    """Finish the round so the room reports FINISHED."""
    white = room.connection_for(Color.WHITE)
    black = room.connection_for(Color.BLACK)
    assert white is not None and black is not None

    for index, (x, y) in enumerate([(3, 7), (4, 7), (5, 7), (6, 7), (7, 7)]):
        result = await room.make_move(white, x, y)
        if result.is_game_over:
            return
        await room.make_move(black, *FILLERS[index])


# ----------------------------------------------------------------------
# creating rooms
# ----------------------------------------------------------------------
async def test_create_room_generates_the_id_and_seats_the_creator() -> None:
    manager = RoomManager(GameSettings(board_size=19))

    room, join = await manager.create_room("conn-1", "  Ｇａｍｅ 방  ")

    assert room.room_id == "room_001"
    assert room.room_name == "Game 방"
    assert room.board_size == 19
    assert join.player.color is Color.WHITE
    assert join.game_started is False
    assert manager.get_room(room.room_id) is room
    assert manager.get_room_count() == 1
    assert manager.get_player_count() == 1


async def test_room_ids_increase_and_are_never_reused() -> None:
    manager = RoomManager()

    first, _ = await manager.create_room("conn-1", "First")
    second, _ = await manager.create_room("conn-2", "Second")
    assert [first.room_id, second.room_id] == ["room_001", "room_002"]

    # Removing a room does not free its number.
    await manager.leave_room(first.room_id, "conn-1")
    third, _ = await manager.create_room("conn-3", "Third")

    assert third.room_id == "room_003"
    assert manager.get_room("room_001") is None


async def test_each_room_snapshots_the_current_settings() -> None:
    manager = RoomManager(GameSettings(board_size=15))
    room_a, _ = await manager.create_room("conn-1", "Small")

    manager.update_settings(GameSettings(board_size=19))
    room_b, _ = await manager.create_room("conn-2", "Large")

    assert room_a.board_size == 15
    assert room_a.game.board_size == 15
    assert room_b.board_size == 19
    assert manager.settings.board_size == 19


async def test_gomoku_and_othello_rooms_coexist() -> None:
    manager = RoomManager(GameSettings(board_size=19))

    gomoku, _ = await manager.create_room("conn-1", "Gomoku")
    othello, join = await manager.create_room(
        "conn-2", "Othello", GameType.OTHELLO
    )

    assert gomoku.game_type is GameType.GOMOKU
    assert gomoku.board_size == 19
    assert othello.game_type is GameType.OTHELLO
    assert isinstance(othello.game, OthelloGame)
    assert othello.board_size == 8
    assert othello.win_length is None
    assert join.player.color is Color.BLACK
    assert [summary.game_type for summary in manager.get_room_list()] == [
        GameType.GOMOKU, GameType.OTHELLO
    ]


@pytest.mark.parametrize(
    "room_name",
    [None, 7, "", "   ", "가" * 31, "first\nsecond", "bad\x00name", "\ud800"],
)
async def test_invalid_room_names_do_not_create_rooms(room_name: object) -> None:
    manager = RoomManager()

    with pytest.raises(InvalidRoomNameError):
        await manager.create_room("conn-1", room_name)

    assert manager.get_room_count() == 0


async def test_duplicate_room_names_use_nfkc_and_casefold() -> None:
    manager = RoomManager()
    await manager.create_room("conn-1", "  Ｇａｍｅ 방  ")

    with pytest.raises(RoomNameTakenError) as excinfo:
        await manager.create_room("conn-2", "game 방")

    assert excinfo.value.code == "ROOM_NAME_TAKEN"
    assert manager.get_room_count() == 1


async def test_duplicate_room_creation_is_atomic() -> None:
    manager = RoomManager()

    results = await asyncio.gather(
        manager.create_room("conn-1", "Same Name"),
        manager.create_room("conn-2", "same name"),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, RoomNameTakenError) for result in results) == 1
    assert manager.get_room_count() == 1


async def test_room_name_can_be_reused_after_room_removal() -> None:
    manager = RoomManager()
    room, _ = await manager.create_room("conn-1", "Reusable")
    await manager.leave_room(room.room_id, "conn-1")

    replacement, _ = await manager.create_room("conn-2", "reusable")

    assert replacement.room_name == "reusable"


# ----------------------------------------------------------------------
# joining rooms
# ----------------------------------------------------------------------
async def test_join_room_seats_the_second_player_and_starts_the_game() -> None:
    manager = RoomManager()
    room, _ = await manager.create_room("conn-1", "Match")

    same_room, join = await manager.join_room(room.room_id, "conn-2")

    assert same_room is room
    assert join.player.color is Color.BLACK
    assert join.game_started is True
    assert room.game.status is GameStatus.PLAYING
    assert room.game.current_turn is Color.WHITE
    assert manager.get_player_count() == 2


async def test_joining_an_unknown_room_is_an_error() -> None:
    manager = RoomManager()

    with pytest.raises(RoomNotFoundError) as excinfo:
        await manager.join_room("room_999", "conn-1")

    assert excinfo.value.code == "ROOM_NOT_FOUND"
    # No room is created as a side effect.
    assert manager.get_room_count() == 0


async def test_joining_a_full_room_is_an_error() -> None:
    manager = RoomManager()
    room, _, _ = await full_room(manager)

    with pytest.raises(RoomFullError) as excinfo:
        await manager.join_room(room.room_id, "conn-3")

    assert excinfo.value.code == "ROOM_FULL"
    assert room.player_count == MAX_PLAYERS


async def test_two_clients_race_for_the_last_seat() -> None:
    manager = RoomManager()
    room, _ = await manager.create_room("conn-1", "Race")

    results = await asyncio.gather(
        manager.join_room(room.room_id, "conn-2"),
        manager.join_room(room.room_id, "conn-3"),
        return_exceptions=True,
    )
    accepted = [r for r in results if not isinstance(r, BaseException)]
    rejected = [r for r in results if isinstance(r, BaseException)]

    assert len(accepted) == 1
    assert len(rejected) == 1
    assert isinstance(rejected[0], RoomFullError)
    assert room.player_count == 2


# ----------------------------------------------------------------------
# leaving rooms
# ----------------------------------------------------------------------
async def test_leaving_keeps_a_room_with_one_player() -> None:
    manager = RoomManager()
    room, first, second = await full_room(manager)

    result = await manager.leave_room(room.room_id, second)

    assert result is not None
    assert result.player.color is Color.BLACK
    assert result.room_removed is False
    assert manager.get_room(room.room_id) is room
    assert room.player_count == 1
    # The interrupted round is discarded and the room waits again.
    assert room.game.status is GameStatus.WAITING
    assert room.game.move_count == 0
    assert room.lobby_status is GameStatus.WAITING


async def test_the_last_player_leaving_removes_the_room() -> None:
    manager = RoomManager()
    room, first, second = await full_room(manager)

    await manager.leave_room(room.room_id, second)
    result = await manager.leave_room(room.room_id, first)

    assert result is not None
    assert result.room_removed is True
    assert manager.get_room(room.room_id) is None
    assert manager.get_room_count() == 0
    assert manager.get_player_count() == 0


async def test_leaving_an_unknown_room_returns_none() -> None:
    manager = RoomManager()

    assert await manager.leave_room("room_999", "conn-1") is None


async def test_leaving_a_room_a_client_is_not_in_returns_none() -> None:
    manager = RoomManager()
    room, _ = await manager.create_room("conn-1", "Leave Test")

    assert await manager.leave_room(room.room_id, "stranger") is None
    assert room.player_count == 1


async def test_remove_room() -> None:
    manager = RoomManager()
    room, _ = await manager.create_room("conn-1", "Remove Test")

    assert await manager.remove_room(room.room_id) is True
    assert await manager.remove_room(room.room_id) is False
    assert manager.get_room_count() == 0


async def test_require_room() -> None:
    manager = RoomManager()
    room, _ = await manager.create_room("conn-1", "Require Test")

    assert manager.require_room(room.room_id) is room
    with pytest.raises(RoomNotFoundError):
        manager.require_room("room_999")


# ----------------------------------------------------------------------
# room list snapshots
# ----------------------------------------------------------------------
async def test_room_list_is_a_plain_snapshot() -> None:
    manager = RoomManager(GameSettings(board_size=19))
    room, _ = await manager.create_room("conn-1", "Summary Test")

    summaries = manager.get_room_list()

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.room_id == room.room_id
    assert summary.room_name == "Summary Test"
    assert summary.board_size == 19
    assert summary.win_length == 5
    assert summary.players == 1
    assert summary.max_players == MAX_PLAYERS
    assert summary.status is GameStatus.WAITING
    # No internal objects leak into the snapshot.
    assert not hasattr(summary, "game")
    assert not hasattr(summary, "lock")


async def test_room_list_reports_playing_and_finished() -> None:
    manager = RoomManager()
    room, _, _ = await full_room(manager)

    assert manager.get_room_list()[0].status is GameStatus.PLAYING
    assert manager.get_room_list()[0].players == 2

    await play_white_win(room)

    assert manager.get_room_list()[0].status is GameStatus.FINISHED


async def test_room_list_is_sorted_by_room_id() -> None:
    manager = RoomManager()
    for index in range(3):
        await manager.create_room(f"conn-{index}", f"Room {index}")

    ids = [summary.room_id for summary in manager.get_room_list()]

    assert ids == ["room_001", "room_002", "room_003"]


async def test_empty_room_list() -> None:
    manager = RoomManager()

    assert manager.get_room_list() == []
    assert manager.get_room_count() == 0
    assert manager.get_player_count() == 0
