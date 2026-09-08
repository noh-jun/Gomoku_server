"""Unit tests for GameRoom: seats, color assignment, locking and restarts."""

from __future__ import annotations

import asyncio

import pytest

from app.board import Color
from app.config import DEFAULT_BOARD_SIZE, GameSettings
from app.errors import (
    ForbiddenMoveError,
    GameAlreadyFinishedError,
    GameNotStartedError,
    NotInRoomError,
    NotYourTurnError,
    OutOfRangeError,
    PositionOccupiedError,
    RoomFullError,
)
from app.game import GameOverReason, GameStatus, MoveResult
from app.room import GameRoom, RestartResult
from app.rules import ForbiddenType

#: Harmless answers while the other color builds a row.
FILLERS: list[tuple[int, int]] = [(0, 0), (14, 0), (0, 14), (14, 14), (7, 0)]


async def full_room(board_size: int = DEFAULT_BOARD_SIZE) -> tuple[GameRoom, str, str]:
    """Room with two seated players; returns (room, first, second) connections."""
    room = GameRoom("test-room", board_size=board_size)
    first = await room.join("conn-1")
    second = await room.join("conn-2")

    assert first.player.color is Color.WHITE
    assert second.player.color is Color.BLACK
    return room, "conn-1", "conn-2"


def test_turn_remaining_ms_uses_only_server_monotonic_clock(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr("app.room.time.monotonic", lambda: now[0])
    room = GameRoom("timer-room", turn_time_limit_sec=30)

    room._start_turn_timer_locked()
    assert room.turn_remaining_ms == 30_000

    now[0] = 112.25
    assert room.turn_remaining_ms == 17_750

    now[0] = 131.0
    assert room.turn_remaining_ms == 0


async def play_white_win(room: GameRoom) -> MoveResult:
    """The current WHITE lines up exactly five and wins."""
    white = room.connection_for(Color.WHITE)
    black = room.connection_for(Color.BLACK)
    assert white is not None and black is not None

    result: MoveResult | None = None
    for index, (x, y) in enumerate([(3, 7), (4, 7), (5, 7), (6, 7), (7, 7)]):
        result = await room.make_move(white, x, y)
        if result.is_game_over:
            break
        await room.make_move(black, *FILLERS[index])
    assert result is not None
    return result


async def play_black_win(room: GameRoom) -> MoveResult:
    """The current BLACK lines up five and wins (WHITE opens each round)."""
    white = room.connection_for(Color.WHITE)
    black = room.connection_for(Color.BLACK)
    assert white is not None and black is not None

    result: MoveResult | None = None
    for index, (x, y) in enumerate([(3, 9), (4, 9), (5, 9), (6, 9), (7, 9)]):
        await room.make_move(white, *FILLERS[index])
        result = await room.make_move(black, x, y)
        if result.is_game_over:
            break
    assert result is not None
    return result


async def setup_white_double_three(room: GameRoom) -> tuple[str, str]:
    """Build the position in front of the double-three point ``(7, 7)``.

    Returns the WHITE and BLACK connection ids; it is WHITE to move.
    """
    white = room.connection_for(Color.WHITE)
    black = room.connection_for(Color.BLACK)
    assert white is not None and black is not None

    for index, (x, y) in enumerate([(5, 7), (6, 7), (7, 5), (7, 6)]):
        await room.make_move(white, x, y)
        await room.make_move(black, *FILLERS[index])
    return white, black


async def restart_both(room: GameRoom) -> RestartResult:
    """Both players agree on a restart; returns the final RestartResult."""
    connections = room.connection_ids()
    await room.request_restart(connections[0])
    return await room.request_restart(connections[1])


# ----------------------------------------------------------------------
# seats and colors
# ----------------------------------------------------------------------
async def test_room_starts_waiting_for_players() -> None:
    room = GameRoom("r1")

    assert room.is_empty is True
    assert room.player_count == 0
    assert room.game.status is GameStatus.WAITING
    assert room.starting_color is Color.WHITE
    assert room.seat_order == (Color.WHITE, Color.BLACK)


async def test_first_player_is_white_second_is_black() -> None:
    room = GameRoom("r1")

    first = await room.join("a")
    assert first.player.color is Color.WHITE
    assert first.game_started is False

    second = await room.join("b")
    assert second.player.color is Color.BLACK
    assert second.game_started is True
    assert room.is_full is True
    assert room.game.status is GameStatus.PLAYING
    assert room.game.current_turn is Color.WHITE
    assert room.player_colors == {"a": Color.WHITE, "b": Color.BLACK}


async def test_color_lookups() -> None:
    room, first, second = await full_room()

    assert room.color_of(first) is Color.WHITE
    assert room.color_of(second) is Color.BLACK
    assert room.color_of("stranger") is None
    assert room.connection_for(Color.WHITE) == first
    assert room.connection_for(Color.BLACK) == second
    assert room.connection_for(None) is None
    assert room.player_for(first) is not None
    assert room.player_for("stranger") is None
    assert [player.connection_id for player in room.players()] == [first, second]


async def test_third_player_is_rejected() -> None:
    room, _, _ = await full_room()

    with pytest.raises(RoomFullError) as excinfo:
        await room.join("conn-third")

    assert excinfo.value.code == "ROOM_FULL"
    assert room.player_count == 2


async def test_white_opens_the_game() -> None:
    room, first, second = await full_room()

    with pytest.raises(NotYourTurnError):
        await room.make_move(second, 7, 7)

    result = await room.make_move(first, 7, 7)
    assert result.color is Color.WHITE
    assert result.next_turn is Color.BLACK


async def test_move_before_the_room_is_full_is_rejected() -> None:
    room = GameRoom("r1")
    joined = await room.join("a")

    with pytest.raises(GameNotStartedError):
        await room.make_move(joined.player.connection_id, 7, 7)


async def test_unknown_connection_cannot_move() -> None:
    room, _, _ = await full_room()

    with pytest.raises(NotInRoomError):
        await room.make_move("stranger", 7, 7)


async def test_leave_frees_the_seat_and_suspends_the_game() -> None:
    room, first, second = await full_room()

    left = await room.leave(first)
    assert left is not None
    assert left.color is Color.WHITE
    assert room.game.status is GameStatus.WAITING
    assert room.free_color() is Color.WHITE

    with pytest.raises(GameNotStartedError):
        await room.make_move(second, 7, 7)

    # The freed seat is handed to the next player, on a fresh board.
    rejoined = await room.join("conn-3")
    assert rejoined.player.color is Color.WHITE
    assert room.game.status is GameStatus.PLAYING
    assert room.game.move_count == 0
    assert room.game.current_turn is Color.WHITE


async def test_leave_with_unknown_connection_returns_none() -> None:
    room, _, _ = await full_room()

    assert await room.leave("stranger") is None
    assert room.player_count == 2


async def test_room_becomes_empty_after_both_leave() -> None:
    room, first, second = await full_room()

    await room.leave(first)
    await room.leave(second)

    assert room.is_empty is True


# ----------------------------------------------------------------------
# concurrency
# ----------------------------------------------------------------------
async def test_simultaneous_moves_on_the_same_cell() -> None:
    """Both players race for (7, 7): exactly one move may be accepted."""
    room, first, second = await full_room()

    results = await asyncio.gather(
        room.make_move(first, 7, 7),
        room.make_move(second, 7, 7),
        return_exceptions=True,
    )
    accepted = [r for r in results if not isinstance(r, BaseException)]
    rejected = [r for r in results if isinstance(r, BaseException)]

    assert len(accepted) == 1
    assert len(rejected) == 1
    assert accepted[0].color is Color.WHITE
    assert isinstance(rejected[0], (NotYourTurnError, PositionOccupiedError))
    assert room.game.move_count == 1
    assert room.game.current_turn is Color.BLACK


async def test_burst_of_moves_from_one_player_is_serialised() -> None:
    room, first, _ = await full_room()

    results = await asyncio.gather(
        *(room.make_move(first, x, 0) for x in range(5)),
        return_exceptions=True,
    )
    accepted = [r for r in results if not isinstance(r, BaseException)]

    assert len(accepted) == 1
    assert all(
        isinstance(r, NotYourTurnError)
        for r in results
        if isinstance(r, BaseException)
    )
    assert room.game.move_count == 1


async def test_no_move_slips_in_after_the_winning_move() -> None:
    """The win and the FINISHED status are set inside the same lock."""
    room, first, second = await full_room()
    white = room.connection_for(Color.WHITE)
    black = room.connection_for(Color.BLACK)
    assert white is not None and black is not None

    for index, (x, y) in enumerate([(3, 7), (4, 7), (5, 7), (6, 7)]):
        await room.make_move(white, x, y)
        await room.make_move(black, *FILLERS[index])

    results = await asyncio.gather(
        room.make_move(white, 7, 7),  # wins
        room.make_move(black, 8, 8),
        room.make_move(white, 9, 9),
        return_exceptions=True,
    )
    accepted = [r for r in results if not isinstance(r, BaseException)]

    assert len(accepted) == 1
    assert accepted[0].winner is Color.WHITE
    assert all(
        isinstance(r, (GameAlreadyFinishedError, NotYourTurnError))
        for r in results
        if isinstance(r, BaseException)
    )
    assert room.game.status is GameStatus.FINISHED
    assert room.game.move_count == 9


# ----------------------------------------------------------------------
# results
# ----------------------------------------------------------------------
async def test_winner_and_loser_are_recorded() -> None:
    room, _, _ = await full_room()

    result = await play_white_win(room)

    assert result.winner is Color.WHITE
    assert result.loser is Color.BLACK
    assert result.reason is GameOverReason.FIVE_IN_A_ROW
    assert room.game.winner is Color.WHITE
    assert room.game.loser is Color.BLACK
    assert room.game.current_turn is None


async def test_a_forbidden_point_is_rejected_under_the_room_lock() -> None:
    room, _, _ = await full_room()
    white, _black = await setup_white_double_three(room)

    with pytest.raises(ForbiddenMoveError) as excinfo:
        await room.make_move(white, 7, 7)

    assert excinfo.value.forbidden_type is ForbiddenType.DOUBLE_THREE
    assert (excinfo.value.x, excinfo.value.y) == (7, 7)
    # The room is untouched: same turn, same board, game still running.
    assert room.game.status is GameStatus.PLAYING
    assert room.game.current_turn is Color.WHITE
    assert room.game.stone_at(7, 7) is None
    assert room.lobby_status is GameStatus.PLAYING


async def test_the_room_keeps_playing_after_a_rejection() -> None:
    room, _, _ = await full_room()
    white, black = await setup_white_double_three(room)

    with pytest.raises(ForbiddenMoveError):
        await room.make_move(white, 7, 7)

    result = await room.make_move(white, 2, 2)
    assert result.next_turn is Color.BLACK

    result = await room.make_move(black, 3, 3)
    assert result.next_turn is Color.WHITE


async def test_moves_after_the_result_are_rejected() -> None:
    room, first, second = await full_room()
    await play_white_win(room)

    for connection in (first, second):
        with pytest.raises(GameAlreadyFinishedError):
            await room.make_move(connection, 1, 1)


# ----------------------------------------------------------------------
# restart and color reassignment
# ----------------------------------------------------------------------
async def test_restart_requires_both_players() -> None:
    room, first, second = await full_room()
    await room.make_move(first, 7, 7)

    pending = await room.request_restart(first)
    assert pending.restarted is False
    assert pending.requested == 1
    assert pending.required == 2
    assert room.game.move_count == 1

    # A repeated request from the same player does not count twice.
    again = await room.request_restart(first)
    assert again.restarted is False
    assert again.requested == 1

    done = await room.request_restart(second)
    assert done.restarted is True
    assert room.restart_requests == set()
    assert room.game.status is GameStatus.PLAYING
    assert room.game.current_turn is Color.WHITE
    assert room.game.move_count == 0
    assert all(cell is None for row in room.game.board for cell in row)


async def test_loser_starts_the_next_round() -> None:
    """WHITE wins, so the BLACK player becomes WHITE for the next round."""
    room, first, second = await full_room()
    await play_white_win(room)

    result = await restart_both(room)

    assert result.restarted is True
    assert result.previous_loser_color is Color.BLACK
    assert result.colors_swapped is True
    assert room.color_of(second) is Color.WHITE
    assert room.color_of(first) is Color.BLACK
    assert result.colors == {first: Color.BLACK, second: Color.WHITE}
    assert room.game.current_turn is Color.WHITE
    assert room.game.starting_color is Color.WHITE
    # The new WHITE (previous loser) may open the round.
    assert (await room.make_move(second, 7, 7)).color is Color.WHITE


async def test_colors_stay_when_the_starting_player_loses() -> None:
    """BLACK wins, so WHITE lost and already holds the starting color."""
    room, first, second = await full_room()
    await play_black_win(room)

    result = await restart_both(room)

    assert result.previous_loser_color is Color.WHITE
    assert result.colors_swapped is False
    assert room.color_of(first) is Color.WHITE
    assert room.color_of(second) is Color.BLACK
    assert (await room.make_move(first, 7, 7)).color is Color.WHITE


async def test_a_rejection_does_not_affect_the_restart_colors() -> None:
    """A blocked point decides nothing, so the round is still open."""
    room, first, second = await full_room()
    white, _black = await setup_white_double_three(room)

    with pytest.raises(ForbiddenMoveError):
        await room.make_move(white, 7, 7)

    result = await restart_both(room)

    assert room.game.winner is None
    assert result.previous_loser_color is None
    assert result.colors_swapped is False
    assert room.color_of(first) is Color.WHITE
    assert room.color_of(second) is Color.BLACK


async def test_colors_survive_two_rounds() -> None:
    room, first, second = await full_room()

    await play_white_win(room)  # first wins as WHITE
    await restart_both(room)
    assert room.color_of(second) is Color.WHITE

    await play_white_win(room)  # second wins as WHITE
    await restart_both(room)
    # The loser of round two (first, playing BLACK) starts round three.
    assert room.color_of(first) is Color.WHITE
    assert room.color_of(second) is Color.BLACK


async def test_draw_keeps_the_colors() -> None:
    room = GameRoom("draw-room", board_size=4)
    await room.join("a")
    await room.join("b")

    result = None
    for y in range(4):
        for x in range(4):
            turn = room.game.current_turn
            assert turn is not None
            connection = room.connection_for(turn)
            assert connection is not None
            result = await room.make_move(connection, x, y)

    assert result is not None
    assert result.reason is GameOverReason.DRAW

    restart = await restart_both(room)

    assert restart.previous_loser_color is None
    assert restart.colors_swapped is False
    assert room.color_of("a") is Color.WHITE
    assert room.color_of("b") is Color.BLACK


async def test_restart_clears_the_previous_result() -> None:
    room, _, _ = await full_room()
    await play_white_win(room)

    await restart_both(room)

    assert room.game.winner is None
    assert room.game.loser is None
    assert room.game.reason is None
    assert room.game.forbidden_moves == {}
    assert room.game.status is GameStatus.PLAYING


async def test_restart_needs_two_connected_players() -> None:
    room = GameRoom("r1")
    joined = await room.join("a")

    with pytest.raises(GameNotStartedError):
        await room.request_restart(joined.player.connection_id)


async def test_restart_votes_are_dropped_when_a_player_leaves() -> None:
    room, first, second = await full_room()
    await room.request_restart(first)
    assert room.restart_requests == {first}

    await room.leave(second)
    assert room.restart_requests == set()


async def test_unknown_connection_cannot_request_restart() -> None:
    room, _, _ = await full_room()

    with pytest.raises(NotInRoomError):
        await room.request_restart("stranger")


# ----------------------------------------------------------------------
# board geometry per room
# ----------------------------------------------------------------------
async def test_room_uses_the_configured_board_size() -> None:
    room = GameRoom("r19", board_size=19, win_length=5)

    assert room.board_size == 19
    assert room.win_length == 5
    assert room.game.board_size == 19
    assert room.settings == GameSettings(
        board_size=19, win_length=5, starting_color=Color.WHITE
    )


async def test_room_from_settings() -> None:
    room = GameRoom.from_settings(
        "r19", GameSettings(board_size=19, win_length=5, starting_color=Color.WHITE)
    )

    assert room.board_size == 19
    assert room.game.settings == room.settings


async def test_far_corner_depends_on_the_room_size() -> None:
    big, big_first, _ = await full_room(board_size=19)
    result = await big.make_move(big_first, 18, 18)
    assert (result.x, result.y) == (18, 18)

    small, small_first, _ = await full_room(board_size=15)
    with pytest.raises(OutOfRangeError):
        await small.make_move(small_first, 18, 18)


async def test_restart_keeps_the_board_size() -> None:
    room, first, second = await full_room(board_size=19)
    await room.make_move(first, 18, 18)

    await restart_both(room)

    assert room.board_size == 19
    assert room.game.board_size == 19
    assert len(room.game.board) == 19
    assert all(len(row) == 19 for row in room.game.board)


async def test_rejoin_keeps_the_board_size() -> None:
    room, _, second = await full_room(board_size=19)
    await room.leave(second)

    await room.join("conn-3")

    assert room.game.board_size == 19
    assert room.game.status is GameStatus.PLAYING
    assert room.game.move_count == 0


# ----------------------------------------------------------------------
# lobby view of a room
# ----------------------------------------------------------------------
async def test_summary_reports_the_lobby_view() -> None:
    room = GameRoom("room_001", board_size=19)

    empty = room.summary()
    assert empty.room_id == "room_001"
    assert empty.board_size == 19
    assert empty.win_length == 5
    assert empty.players == 0
    assert empty.max_players == 2
    assert empty.status is GameStatus.WAITING

    await room.join("a")
    assert room.summary().players == 1
    assert room.lobby_status is GameStatus.WAITING

    await room.join("b")
    assert room.summary().players == 2
    assert room.lobby_status is GameStatus.PLAYING


async def test_lobby_status_follows_the_game() -> None:
    room, _, _ = await full_room()
    assert room.lobby_status is GameStatus.PLAYING

    await play_white_win(room)
    assert room.lobby_status is GameStatus.FINISHED
    assert room.summary().status is GameStatus.FINISHED

    await restart_both(room)
    assert room.lobby_status is GameStatus.PLAYING


async def test_leave_discards_the_running_round() -> None:
    room, first, second = await full_room()
    await room.make_move(first, 7, 7)

    await room.leave(second)

    # The board is cleared: the remaining player waits for a new opponent.
    assert room.game.status is GameStatus.WAITING
    assert room.game.move_count == 0
    assert all(cell is None for row in room.game.board for cell in row)
    assert room.game.current_turn is Color.WHITE
    assert room.lobby_status is GameStatus.WAITING


async def test_leave_after_a_result_clears_the_result() -> None:
    room, first, second = await full_room()
    await play_white_win(room)

    await room.leave(second)

    assert room.game.winner is None
    assert room.game.loser is None
    assert room.game.reason is None
    assert room.game.status is GameStatus.WAITING
