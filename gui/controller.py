"""Server lifecycle control, decoupled from any user interface.

The controller owns the uvicorn server and the thread it runs in:

```text
Main thread    -> Tkinter GUI (never blocks)
Server thread  -> uvicorn -> FastAPI -> WebSocket
```

It contains no game logic and no widget code, so it can be driven from the
GUI, from a test, or from any other front end.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Optional

import uvicorn
from fastapi import FastAPI

from app.config import ServerConfig
from app.main import create_app
from app.room import RoomSummary

logger = logging.getLogger("gomoku.server")


class ServerAlreadyRunningError(RuntimeError):
    """Raised when Start is triggered while a server is already running."""


@dataclass(frozen=True)
class ServerStats:
    """Snapshot of the numbers shown in the GUI.

    ``connections`` counts every WebSocket (lobby included), ``lobby`` the
    ones outside a room and ``players`` the seats taken inside rooms.
    """

    connections: int = 0
    lobby: int = 0
    players: int = 0
    rooms: int = 0
    accounts: int = 0
    online_users: int = 0


@dataclass(frozen=True)
class AccountRow:
    """Password-free account data exposed to the management GUI."""

    account_id: str
    nickname: str
    created_at: str


@dataclass(frozen=True)
class OnlineUserRow:
    """One authenticated WebSocket connection shown in the management GUI."""

    connection_id: str
    account_id: str
    nickname: str


class AccountDeleteResult:
    DELETED = "DELETED"
    ONLINE = "ONLINE"
    NOT_FOUND = "NOT_FOUND"


class QueueLogHandler(logging.Handler):
    """Logging handler that forwards formatted records to a queue.

    The server thread only ever puts strings into a thread-safe queue; the
    GUI thread drains it from ``root.after`` and updates the widgets.  No
    widget is ever touched from the server thread.
    """

    def __init__(
        self, log_queue: "queue.Queue[str]", level: int = logging.INFO
    ) -> None:
        super().__init__(level)
        self._queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(self.format(record))
        except queue.Full:
            # Dropping a GUI log line is better than blocking the server.
            pass
        except (ValueError, TypeError):  # pragma: no cover - formatting bug
            self.handleError(record)


class ServerController:
    """Starts and stops the uvicorn server in a background thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[uvicorn.Server] = None
        self._app: Optional[FastAPI] = None
        self._config: Optional[ServerConfig] = None
        self._error: Optional[BaseException] = None

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        """``True`` while the server thread is alive."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def is_serving(self) -> bool:
        """``True`` once uvicorn finished startup and accepts connections."""
        server = self._server
        return bool(server is not None and server.started) and self.is_running

    @property
    def config(self) -> Optional[ServerConfig]:
        """Configuration the running (or last) server was started with."""
        return self._config

    @property
    def error(self) -> Optional[BaseException]:
        """Failure of the last run, e.g. the port was already in use."""
        return self._error

    def get_stats(self) -> ServerStats:
        """Connection/room counts, read through count-only accessors.

        The GUI never iterates the internal dictionaries of the server.
        """
        app = self._app
        if app is None or not self.is_running:
            return ServerStats()
        manager = getattr(app.state, "manager", None)
        rooms = getattr(app.state, "rooms", None)
        accounts = getattr(app.state, "accounts", None)
        if manager is None or rooms is None or accounts is None:  # pragma: no cover
            return ServerStats()
        return ServerStats(
            connections=manager.get_connection_count(),
            lobby=manager.get_lobby_count(),
            players=rooms.get_player_count(),
            rooms=rooms.get_room_count(),
            accounts=accounts.get_account_count() if self.is_serving else 0,
            online_users=len(manager.get_online_users()),
        )

    def get_rooms(self) -> list[RoomSummary]:
        """Room list snapshot for the GUI table (plain value objects)."""
        app = self._app
        if app is None or not self.is_running:
            return []
        rooms = getattr(app.state, "rooms", None)
        if rooms is None:  # pragma: no cover
            return []
        return rooms.get_room_list()

    def get_accounts(self) -> list[AccountRow]:
        """Return password-free account rows for the GUI."""
        app = self._app
        if app is None or not self.is_serving:
            return []
        accounts = getattr(app.state, "accounts", None)
        if accounts is None:  # pragma: no cover
            return []
        return [
            AccountRow(account.account_id, account.nickname, account.created_at)
            for account in accounts.list_accounts()
        ]

    def get_online_users(self) -> list[OnlineUserRow]:
        """Return one row for every authenticated live connection."""
        app = self._app
        if app is None or not self.is_running:
            return []
        manager = getattr(app.state, "manager", None)
        if manager is None:  # pragma: no cover
            return []
        return [
            OnlineUserRow(user.connection_id, user.account_id, user.nickname)
            for user in manager.get_online_users()
        ]

    def delete_account(self, account_id: str) -> str:
        """Delete an offline account from the running server database."""
        app = self._app
        if app is None or not self.is_serving:
            return AccountDeleteResult.NOT_FOUND
        manager = getattr(app.state, "manager", None)
        accounts = getattr(app.state, "accounts", None)
        if manager is None or accounts is None:  # pragma: no cover
            return AccountDeleteResult.NOT_FOUND
        if any(
            user.account_id == account_id for user in manager.get_online_users()
        ):
            return AccountDeleteResult.ONLINE
        return (
            AccountDeleteResult.DELETED
            if accounts.delete_account(account_id)
            else AccountDeleteResult.NOT_FOUND
        )

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self, config: ServerConfig) -> None:
        """Start the server in a new thread.

        Raises :class:`ServerAlreadyRunningError` when one is already up, so
        pressing Start twice can never create a second server.
        """
        with self._lock:
            if self.is_running:
                raise ServerAlreadyRunningError("The server is already running.")

            self._error = None
            app = create_app(config)
            uvicorn_config = uvicorn.Config(
                app=app,
                host=config.host,
                port=config.port,
                log_level="info",
                access_log=False,
                log_config=None,
            )
            server = uvicorn.Server(uvicorn_config)
            thread = threading.Thread(
                target=self._serve,
                args=(server, config),
                name="gomoku-uvicorn",
                daemon=True,
            )
            self._app = app
            self._server = server
            self._config = config
            self._thread = thread

            logger.info("Server starting on %s", config.address)
            logger.info("Board size: %s", config.board_label)
            thread.start()

    def _serve(self, server: uvicorn.Server, config: ServerConfig) -> None:
        """Thread body: run uvicorn until :meth:`stop` asks it to exit.

        uvicorn only installs signal handlers on the main thread, so running
        it here is safe.
        """
        try:
            server.run()
        except SystemExit as exc:
            # uvicorn exits this way when it cannot bind the address.
            self._error = RuntimeError(
                f"Could not start the server on {config.address} "
                f"(port already in use?)"
            )
            logger.error("Server could not start on %s: %s", config.address, exc)
        except Exception as exc:  # noqa: BLE001 - reported to the operator
            self._error = exc
            logger.exception("Server thread stopped with an error: %s", exc)
        else:
            logger.info("Server stopped")

    def stop(self, timeout: float = 10.0) -> None:
        """Ask uvicorn to shut down gracefully and wait for the thread.

        No thread is ever killed: uvicorn closes its WebSocket connections
        and the event loop on its own.
        """
        with self._lock:
            server, thread = self._server, self._thread
            if server is None or thread is None:
                return

            if thread.is_alive():
                logger.info("Server shutting down...")
                server.should_exit = True
                thread.join(timeout)
                if thread.is_alive():
                    logger.warning(
                        "Graceful shutdown timed out after %.1fs, forcing exit",
                        timeout,
                    )
                    server.force_exit = True
                    thread.join(timeout)

            self._server = None
            self._thread = None
            self._app = None
