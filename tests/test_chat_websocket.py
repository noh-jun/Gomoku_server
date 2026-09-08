"""Room chat over the real WebSocket endpoint.

Every client logs in with its own account so that ``chat_message`` carries a
real nickname, then rooms are created and joined with the normal lobby flow.
Frames are consumed with ``drain_until`` because the exact frame order around
join/leave (``room_members``, ``room_list`` broadcasts) is not what chat is
about.
"""

from __future__ import annotations

import time
from contextlib import ExitStack
from itertools import count
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import ServerConfig
from app.main import create_app

ACCOUNT_SEQUENCE = count(1)
ROOM_SEQUENCE = count(1)
MAX_FRAMES = 20


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    config = ServerConfig(account_db_path=tmp_path / "accounts.db")
    with TestClient(create_app(config)) as test_client:
        yield test_client


@pytest.fixture
def sockets(client: TestClient) -> Iterator[ExitStack]:
    """Closes every opened socket before ``client`` shuts the app down.

    A WebSocket session left open keeps its endpoint task alive, and
    ``TestClient.__exit__`` then waits for it forever.
    """
    with ExitStack() as stack:
        yield stack


def drain_until(ws: Any, message_type: str, limit: int = MAX_FRAMES) -> dict[str, Any]:
    """Receive frames until one of ``message_type`` arrives and return it.

    An unexpected ``error`` fails immediately instead of blocking on a frame
    that will never come.
    """
    seen: list[str] = []
    for _ in range(limit):
        message = ws.receive_json()
        seen.append(message["type"])
        if message["type"] == message_type:
            return message
        if message["type"] == "error":
            raise AssertionError(f"waiting for {message_type!r} but got {message}")
    raise AssertionError(f"no {message_type!r} within {limit} frames, saw {seen}")


def next_account_id() -> str:
    """Account IDs must be English letters only, so count in base 26."""
    number = next(ACCOUNT_SEQUENCE)
    suffix = chr(ord("a") + number % 26) + chr(ord("a") + (number // 26) % 26)
    return f"chatuser{suffix}"


def connect_and_login(sockets: ExitStack, client: TestClient, nickname: str) -> Any:
    """Open a socket, create a fresh account for it and log in."""
    ws = sockets.enter_context(client.websocket_connect("/ws"))
    drain_until(ws, "connected")
    account_id = next_account_id()
    ws.send_json(
        {
            "type": "create_account",
            "account_id": account_id,
            "password": "pass1234",
            "nickname": nickname,
        }
    )
    drain_until(ws, "account_created")
    ws.send_json({"type": "login", "account_id": account_id, "password": "pass1234"})
    login = drain_until(ws, "login_succeeded")
    assert login["nickname"] == nickname
    return ws


def create_room(ws: Any) -> str:
    ws.send_json({"type": "create_room", "room_name": f"Chat Room {next(ROOM_SEQUENCE)}"})
    return drain_until(ws, "joined")["room_id"]


def join_room(ws: Any, room_id: str) -> None:
    ws.send_json({"type": "join_room", "room_id": room_id})
    assert drain_until(ws, "joined")["room_id"] == room_id


def test_chat_is_echoed_to_every_room_member_including_the_sender(
    client: TestClient, sockets: ExitStack
) -> None:
    host = connect_and_login(sockets, client, "돌쇠")
    guest = connect_and_login(sockets, client, "마당쇠")
    watcher = connect_and_login(sockets, client, "관전자A")
    room_id = create_room(host)
    join_room(guest, room_id)
    join_room(watcher, room_id)

    host.send_json({"type": "chat", "text": "  준비됐어요  "})
    before = int(time.time() * 1000)
    for ws in (host, guest, watcher):
        frame = drain_until(ws, "chat_message")
        assert frame["nickname"] == "돌쇠"
        assert frame["text"] == "준비됐어요"
        assert isinstance(frame["sent_at_unix_ms"], int)
        assert before - 5_000 <= frame["sent_at_unix_ms"] <= before + 5_000
        assert set(frame) == {"type", "nickname", "text", "sent_at_unix_ms"}

    watcher.send_json({"type": "chat", "text": "관전자도 말할 수 있어요"})
    for ws in (host, guest, watcher):
        frame = drain_until(ws, "chat_message")
        assert (frame["nickname"], frame["text"]) == ("관전자A", "관전자도 말할 수 있어요")


def test_chat_outside_a_room_is_rejected_and_nothing_is_broadcast(
    client: TestClient, sockets: ExitStack
) -> None:
    host = connect_and_login(sockets, client, "돌쇠")
    lobby_user = connect_and_login(sockets, client, "로비사람")
    room_id = create_room(host)

    lobby_user.send_json({"type": "chat", "text": "여기 아무도 없나요"})
    error = drain_until(lobby_user, "error")
    assert error["code"] == "CHAT_NOT_AVAILABLE"

    # The room never sees the lobby message: the next chat frame in the room
    # is the marker the host sends afterwards.
    host.send_json({"type": "chat", "text": "marker"})
    assert drain_until(host, "chat_message")["text"] == "marker"
    del room_id


def test_unauthenticated_connection_cannot_chat(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        drain_until(ws, "connected")
        ws.send_json({"type": "chat", "text": "hello"})
        assert drain_until(ws, "error")["code"] == "CHAT_NOT_AVAILABLE"
        ws.send_json({"type": "ping"})
        drain_until(ws, "pong")  # the socket survives the rejection


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "chat"},
        {"type": "chat", "text": ""},
        {"type": "chat", "text": "   "},
        {"type": "chat", "text": "x" * 201},
        {"type": "chat", "text": "줄\n바꿈"},
        {"type": "chat", "text": 123},
    ],
)
def test_invalid_text_is_rejected_for_the_sender_only(
    client: TestClient, sockets: ExitStack, payload: dict[str, Any]
) -> None:
    host = connect_and_login(sockets, client, "돌쇠")
    guest = connect_and_login(sockets, client, "마당쇠")
    join_room(guest, create_room(host))

    host.send_json(payload)
    assert drain_until(host, "error")["code"] == "CHAT_TEXT_INVALID"

    host.send_json({"type": "chat", "text": "marker"})
    assert drain_until(guest, "chat_message")["text"] == "marker"
    assert drain_until(host, "chat_message")["text"] == "marker"


def test_rate_limit_allows_two_per_second_and_recovers(
    client: TestClient, sockets: ExitStack
) -> None:
    host = connect_and_login(sockets, client, "돌쇠")
    guest = connect_and_login(sockets, client, "마당쇠")
    join_room(guest, create_room(host))

    for text in ("one", "two", "three"):
        host.send_json({"type": "chat", "text": text})
    assert drain_until(host, "chat_message")["text"] == "one"
    assert drain_until(host, "chat_message")["text"] == "two"
    assert drain_until(host, "error")["code"] == "CHAT_RATE_LIMITED"

    time.sleep(1.05)
    host.send_json({"type": "chat", "text": "four"})
    assert drain_until(host, "chat_message")["text"] == "four"

    # The guest saw exactly the accepted messages, in order, and never "three".
    assert [drain_until(guest, "chat_message")["text"] for _ in range(3)] == ["one", "two", "four"]


def test_leaving_the_room_stops_delivery(client: TestClient, sockets: ExitStack) -> None:
    host = connect_and_login(sockets, client, "돌쇠")
    guest = connect_and_login(sockets, client, "마당쇠")
    join_room(guest, create_room(host))

    guest.send_json({"type": "leave_room"})
    drain_until(guest, "left_room")
    guest.send_json({"type": "chat", "text": "나갔는데요"})
    assert drain_until(guest, "error")["code"] == "CHAT_NOT_AVAILABLE"

    host.send_json({"type": "chat", "text": "혼자 남았네"})
    assert drain_until(host, "chat_message")["text"] == "혼자 남았네"
    guest.send_json({"type": "ping"})
    # Everything the guest receives after leaving is lobby traffic, never chat.
    pong_seen = False
    for _ in range(MAX_FRAMES):
        frame = guest.receive_json()
        assert frame["type"] != "chat_message", frame
        if frame["type"] == "pong":
            pong_seen = True
            break
    assert pong_seen
