"""Tests for the threaded server controller (no GUI, no Tkinter needed)."""

from __future__ import annotations

import logging
import queue
import socket
import time
from typing import Callable

import httpx
import pytest

from app.config import ServerConfig
from gui.controller import (
    QueueLogHandler,
    ServerAlreadyRunningError,
    ServerController,
    ServerStats,
)


def free_port() -> int:
    """Ask the OS for a port that is currently unused."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_until(
    predicate: Callable[[], bool], timeout: float = 20.0, interval: float = 0.05
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_start_and_stop_a_19x19_server() -> None:
    config = ServerConfig(host="127.0.0.1", port=free_port(), board_size=19)
    controller = ServerController()

    try:
        controller.start(config)
        assert wait_until(lambda: controller.is_serving), "server never started"
        assert controller.is_running is True
        assert controller.config == config

        response = httpx.get(f"http://{config.address}/health", timeout=5.0)
        assert response.status_code == 200
        payload = response.json()
        assert payload["board_size"] == 19
        assert payload["rooms"] == 0
        assert payload["connections"] == 0

        # Pressing Start twice must not create a second server.
        with pytest.raises(ServerAlreadyRunningError):
            controller.start(config)
    finally:
        controller.stop()

    assert controller.is_running is False
    assert controller.is_serving is False
    assert controller.error is None


def test_the_server_can_be_restarted_with_another_board_size() -> None:
    controller = ServerController()
    try:
        for board_size in (15, 19):
            config = ServerConfig(
                host="127.0.0.1", port=free_port(), board_size=board_size
            )
            controller.start(config)
            assert wait_until(lambda: controller.is_serving), "server never started"

            response = httpx.get(f"http://{config.address}/", timeout=5.0)
            assert response.json()["board_size"] == board_size

            controller.stop()
            assert controller.is_running is False
    finally:
        controller.stop()


def test_stats_and_stop_are_safe_while_stopped() -> None:
    controller = ServerController()

    assert controller.is_running is False
    assert controller.is_serving is False
    assert controller.get_stats() == ServerStats(
        connections=0, lobby=0, players=0, rooms=0
    )
    assert controller.get_rooms() == []
    assert controller.config is None

    controller.stop()  # must not raise
    assert controller.is_running is False


def test_queue_log_handler_forwards_formatted_records() -> None:
    log_queue: "queue.Queue[str]" = queue.Queue()
    handler = QueueLogHandler(log_queue)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("test.queue.handler")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    try:
        logger.info("[ROOM %s] BLACK MOVE (%d, %d)", "abc123", 9, 9)
    finally:
        logger.removeHandler(handler)

    assert log_queue.get_nowait() == "INFO [ROOM abc123] BLACK MOVE (9, 9)"
    assert log_queue.empty() is True


def test_queue_log_handler_drops_lines_instead_of_blocking() -> None:
    log_queue: "queue.Queue[str]" = queue.Queue(maxsize=1)
    handler = QueueLogHandler(log_queue)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test.queue.full")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    try:
        logger.info("first")
        logger.info("second")  # queue is full: dropped, never raises
    finally:
        logger.removeHandler(handler)

    assert log_queue.get_nowait() == "first"
    assert log_queue.empty() is True
