"""Tkinter server management GUI.

Runs on the main thread and never blocks: the server itself lives in a
worker thread owned by :class:`~gui.controller.ServerController`, and the
GUI only communicates with it through a :class:`queue.Queue` of log lines
plus periodic ``root.after`` polling.  No widget is touched from the server
thread, and no game logic lives in this module.
"""

from __future__ import annotations

import logging
import queue
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Optional

from app.config import (
    RULE_NAME,
    SUPPORTED_BOARD_SIZES,
    ConfigError,
    ServerConfig,
)
from app.log import configure_logging

from .controller import (
    AccountDeleteResult,
    QueueLogHandler,
    ServerAlreadyRunningError,
    ServerController,
)

logger = logging.getLogger("gomoku.gui")

GUI_LOG_FORMAT = "%(asctime)s %(message)s"
GUI_LOG_DATE_FORMAT = "%H:%M:%S"

STATE_STOPPED = "STOPPED"
STATE_STARTING = "STARTING"
STATE_RUNNING = "RUNNING"

STATE_COLORS = {
    STATE_STOPPED: "#a11",
    STATE_STARTING: "#a70",
    STATE_RUNNING: "#161",
}


class ServerGUI:
    """The server management window."""

    POLL_LOG_MS = 120
    POLL_STATUS_MS = 400
    MAX_LOG_LINES = 1000

    def __init__(
        self,
        root: tk.Tk,
        controller: ServerController,
        log_queue: "queue.Queue[str]",
        config: Optional[ServerConfig] = None,
    ) -> None:
        self._root = root
        self._controller = controller
        self._log_queue = log_queue
        self._state = STATE_STOPPED
        self._closing = False

        initial = config or ServerConfig()
        self._host_var = tk.StringVar(value=initial.host)
        self._port_var = tk.StringVar(value=str(initial.port))
        self._board_size_var = tk.IntVar(value=initial.board_size)
        self._status_var = tk.StringVar(value=STATE_STOPPED)
        self._connections_var = tk.StringVar(value="0")
        self._lobby_var = tk.StringVar(value="0")
        self._players_var = tk.StringVar(value="0")
        self._rooms_var = tk.StringVar(value="0")
        self._accounts_var = tk.StringVar(value="0")
        self._online_users_var = tk.StringVar(value="0")

        root.title("Gomoku Server")
        root.minsize(560, 680)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        notebook = ttk.Notebook(root)
        notebook.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self._servers_tab = ttk.Frame(notebook)
        self._rooms_tab = ttk.Frame(notebook)
        self._accounts_tab = ttk.Frame(notebook)
        self._online_users_tab = ttk.Frame(notebook)
        notebook.add(self._servers_tab, text="Servers")
        notebook.add(self._rooms_tab, text="Rooms")
        notebook.add(self._accounts_tab, text="Accounts")
        notebook.add(self._online_users_tab, text="Online Users")

        self._servers_tab.columnconfigure(0, weight=1)
        self._servers_tab.rowconfigure(2, weight=1)
        for tab in (self._rooms_tab, self._accounts_tab, self._online_users_tab):
            tab.columnconfigure(0, weight=1)
            tab.rowconfigure(0, weight=1)

        self._build_config_frame(self._servers_tab)
        self._build_status_frame(self._servers_tab)
        self._build_log_frame(self._servers_tab)
        self._build_rooms_frame(self._rooms_tab)
        self._build_accounts_frame(self._accounts_tab)
        self._build_online_users_frame(self._online_users_tab)
        self._apply_state(STATE_STOPPED)

        self._root.after(self.POLL_LOG_MS, self._poll_log_queue)
        self._root.after(self.POLL_STATUS_MS, self._poll_status)

    # ------------------------------------------------------------------
    # widgets
    # ------------------------------------------------------------------
    def _build_config_frame(self, root: tk.Tk) -> None:
        frame = ttk.LabelFrame(root, text="Configuration", padding=10)
        frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Host:").grid(row=0, column=0, sticky="w")
        self._host_entry = ttk.Entry(frame, textvariable=self._host_var)
        self._host_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(frame, text="Port:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._port_entry = ttk.Entry(frame, textvariable=self._port_var, width=10)
        self._port_entry.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

        ttk.Label(frame, text="Board Size:").grid(
            row=2, column=0, sticky="nw", pady=(10, 0)
        )
        board_frame = ttk.Frame(frame)
        board_frame.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        self._board_buttons: list[ttk.Radiobutton] = []
        for size in SUPPORTED_BOARD_SIZES:
            button = ttk.Radiobutton(
                board_frame,
                text=f"{size} x {size}",
                value=size,
                variable=self._board_size_var,
            )
            button.pack(anchor="w")
            self._board_buttons.append(button)

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self._start_button = ttk.Button(
            buttons, text="Start Server", command=self.on_start
        )
        self._start_button.pack(side="left")
        self._stop_button = ttk.Button(
            buttons, text="Stop Server", command=self.on_stop
        )
        self._stop_button.pack(side="left", padx=(8, 0))

    def _build_status_frame(self, root: tk.Tk) -> None:
        frame = ttk.LabelFrame(root, text="Status", padding=10)
        frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)

        ttk.Label(frame, text="Status:").grid(row=0, column=0, sticky="w")
        self._status_label = ttk.Label(
            frame, textvariable=self._status_var, foreground=STATE_COLORS[STATE_STOPPED]
        )
        self._status_label.grid(row=0, column=1, sticky="w", padx=(8, 20))

        # Connections counts every socket (lobby included); Players counts
        # the seats taken inside rooms.
        ttk.Label(frame, text="Connections:").grid(row=1, column=0, sticky="w")
        ttk.Label(frame, textvariable=self._connections_var).grid(
            row=1, column=1, sticky="w", padx=(8, 20)
        )

        ttk.Label(frame, text="In Lobby:").grid(row=2, column=0, sticky="w")
        ttk.Label(frame, textvariable=self._lobby_var).grid(
            row=2, column=1, sticky="w", padx=(8, 20)
        )

        ttk.Label(frame, text="Players in Rooms:").grid(row=3, column=0, sticky="w")
        ttk.Label(frame, textvariable=self._players_var).grid(
            row=3, column=1, sticky="w", padx=(8, 20)
        )

        ttk.Label(frame, text="Active Rooms:").grid(row=4, column=0, sticky="w")
        ttk.Label(frame, textvariable=self._rooms_var).grid(
            row=4, column=1, sticky="w", padx=(8, 20)
        )

        ttk.Label(frame, text="Games:").grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Label(frame, text="Gomoku / Othello").grid(
            row=5, column=1, sticky="w", padx=(8, 20), pady=(6, 0)
        )

        ttk.Label(frame, text="Gomoku Rule:").grid(row=6, column=0, sticky="w")
        ttk.Label(frame, text=RULE_NAME.replace("_", " ").title()).grid(
            row=6, column=1, sticky="w", padx=(8, 20)
        )

        ttk.Label(frame, text="Accounts:").grid(row=7, column=0, sticky="w")
        ttk.Label(frame, textvariable=self._accounts_var).grid(
            row=7, column=1, sticky="w", padx=(8, 20)
        )

        ttk.Label(frame, text="Online Users:").grid(row=8, column=0, sticky="w")
        ttk.Label(frame, textvariable=self._online_users_var).grid(
            row=8, column=1, sticky="w", padx=(8, 20)
        )

    def _build_rooms_frame(self, root: tk.Tk) -> None:
        frame = ttk.LabelFrame(root, text="Rooms", padding=6)
        frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        columns = ("room", "name", "game", "board", "players", "status")
        self._room_table = ttk.Treeview(
            frame, columns=columns, show="headings", height=6
        )
        for column, heading, width, anchor in (
            ("room", "Room ID", 120, "w"),
            ("name", "Room Name", 180, "w"),
            ("game", "Game", 90, "center"),
            ("board", "Board", 80, "center"),
            ("players", "Players", 80, "center"),
            ("status", "Status", 110, "center"),
        ):
            self._room_table.heading(column, text=heading)
            self._room_table.column(column, width=width, anchor=anchor)
        self._room_table.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(
            frame, orient="vertical", command=self._room_table.yview
        )
        self._room_table.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

    def _build_accounts_frame(self, root: ttk.Frame) -> None:
        frame = ttk.LabelFrame(root, text="Accounts", padding=6)
        frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        columns = ("account_id", "nickname", "created_at")
        self._account_table = ttk.Treeview(frame, columns=columns, show="headings")
        for column, heading, width in (
            ("account_id", "Account ID", 180),
            ("nickname", "Nickname", 180),
            ("created_at", "Created At", 240),
        ):
            self._account_table.heading(column, text=heading)
            self._account_table.column(column, width=width, anchor="w")
        self._account_table.grid(row=0, column=0, sticky="nsew")
        self._account_table.bind(
            "<<TreeviewSelect>>", lambda _event: self._update_delete_account_button()
        )

        scrollbar = ttk.Scrollbar(
            frame, orient="vertical", command=self._account_table.yview
        )
        self._account_table.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

        self._delete_account_button = ttk.Button(
            frame,
            text="Delete Account",
            command=self._delete_selected_account,
            state="disabled",
        )
        self._delete_account_button.grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

    def _build_online_users_frame(self, root: ttk.Frame) -> None:
        frame = ttk.LabelFrame(root, text="Online Users", padding=6)
        frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        columns = ("connection_id", "account_id", "nickname")
        self._online_user_table = ttk.Treeview(
            frame, columns=columns, show="headings"
        )
        for column, heading, width in (
            ("connection_id", "Connection ID", 280),
            ("account_id", "Account ID", 160),
            ("nickname", "Nickname", 160),
        ):
            self._online_user_table.heading(column, text=heading)
            self._online_user_table.column(column, width=width, anchor="w")
        self._online_user_table.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(
            frame, orient="vertical", command=self._online_user_table.yview
        )
        self._online_user_table.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

    def _build_log_frame(self, root: tk.Tk) -> None:
        frame = ttk.LabelFrame(root, text="Server Log", padding=6)
        frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=(5, 10))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self._log_text = scrolledtext.ScrolledText(
            frame, height=16, wrap="none", state="disabled", font=("Consolas", 9)
        )
        self._log_text.grid(row=0, column=0, sticky="nsew")

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def on_start(self) -> None:
        """Read the form, build a config and start the server thread."""
        try:
            config = self.read_config()
        except ConfigError as exc:
            messagebox.showerror("Invalid configuration", str(exc))
            return

        try:
            self._controller.start(config)
        except ServerAlreadyRunningError:
            # Should not happen (the button is disabled), but stay safe.
            messagebox.showinfo("Server", "The server is already running.")
            return

        self._apply_state(STATE_STARTING)

    def on_stop(self) -> None:
        self._apply_state(STATE_STARTING if self._controller.is_running else STATE_STOPPED)
        self._root.update_idletasks()
        self._controller.stop()
        self._apply_state(STATE_STOPPED)

    def on_close(self) -> None:
        """Shut the server down before destroying the window."""
        if self._closing:
            return
        self._closing = True
        if self._controller.is_running:
            logger.info("Window closed, stopping the server...")
            self._root.update_idletasks()
            self._controller.stop()
        self._drain_log_queue()
        self._root.destroy()

    def _delete_selected_account(self) -> None:
        selection = self._account_table.selection()
        if len(selection) != 1 or not self._controller.is_serving:
            return
        account_id = selection[0]
        values = self._account_table.item(account_id, "values")
        nickname = values[1] if len(values) > 1 else ""
        confirmed = messagebox.askyesno(
            "Delete Account",
            f"Delete this account?\n\nAccount ID: {account_id}\nNickname: {nickname}",
            icon="warning",
        )
        if not confirmed:
            return

        try:
            result = self._controller.delete_account(account_id)
        except Exception as exc:  # noqa: BLE001 - report persistence failure
            logger.exception("Could not delete account %s", account_id)
            messagebox.showerror("Delete Account", str(exc))
            return

        if result == AccountDeleteResult.ONLINE:
            messagebox.showwarning(
                "Delete Account",
                "The account is currently online and cannot be deleted.",
            )
        elif result == AccountDeleteResult.NOT_FOUND:
            messagebox.showinfo(
                "Delete Account", "The account no longer exists."
            )
        self._refresh_account_table()
        self._update_delete_account_button()

    def read_config(self) -> ServerConfig:
        """Turn the form values into a validated :class:`ServerConfig`."""
        port_text = self._port_var.get().strip()
        if not port_text.isdigit():
            raise ConfigError("Port must be a positive integer.")
        return ServerConfig(
            host=self._host_var.get().strip(),
            port=int(port_text),
            board_size=int(self._board_size_var.get()),
        )

    # ------------------------------------------------------------------
    # polling (main thread only)
    # ------------------------------------------------------------------
    def _poll_log_queue(self) -> None:
        self._drain_log_queue()
        if not self._closing:
            self._root.after(self.POLL_LOG_MS, self._poll_log_queue)

    def _drain_log_queue(self) -> None:
        lines: list[str] = []
        while True:
            try:
                lines.append(self._log_queue.get_nowait())
            except queue.Empty:
                break
        if lines:
            self._append_log(lines)

    def _append_log(self, lines: list[str]) -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert("end", "\n".join(lines) + "\n")
        # Keep the widget bounded so a long session cannot eat all memory.
        overflow = int(self._log_text.index("end-1c").split(".")[0]) - self.MAX_LOG_LINES
        if overflow > 0:
            self._log_text.delete("1.0", f"{overflow + 1}.0")
        self._log_text.configure(state="disabled")
        self._log_text.see("end")

    def _poll_status(self) -> None:
        if self._closing:
            return

        if self._controller.is_serving:
            state = STATE_RUNNING
        elif self._controller.is_running:
            state = STATE_STARTING
        else:
            state = STATE_STOPPED

        if state != self._state:
            self._on_state_change(self._state, state)
            self._apply_state(state)

        stats = self._controller.get_stats()
        self._connections_var.set(str(stats.connections))
        self._lobby_var.set(str(stats.lobby))
        self._players_var.set(str(stats.players))
        self._rooms_var.set(str(stats.rooms))
        self._accounts_var.set(str(stats.accounts))
        self._online_users_var.set(str(stats.online_users))
        self._refresh_room_table()
        self._refresh_account_table()
        self._refresh_online_user_table()

        self._root.after(self.POLL_STATUS_MS, self._poll_status)

    def _refresh_room_table(self) -> None:
        """Redraw the room table from a plain snapshot of the room list."""
        rows = {
            summary.room_id: (
                summary.room_id,
                summary.room_name,
                summary.game_type.value.title(),
                f"{summary.board_size}x{summary.board_size}",
                f"{summary.players}/{summary.max_players}",
                summary.status.value,
            )
            for summary in self._controller.get_rooms()
        }

        for room_id in set(self._room_table.get_children()) - set(rows):
            self._room_table.delete(room_id)
        for room_id, values in rows.items():
            if self._room_table.exists(room_id):
                self._room_table.item(room_id, values=values)
            else:
                self._room_table.insert("", "end", iid=room_id, values=values)

    def _refresh_account_table(self) -> None:
        """Redraw the password-free account table from the controller snapshot."""
        rows = {
            account.account_id: (
                account.account_id,
                account.nickname,
                account.created_at,
            )
            for account in self._controller.get_accounts()
        }

        for account_id in set(self._account_table.get_children()) - set(rows):
            self._account_table.delete(account_id)
        for account_id, values in rows.items():
            if self._account_table.exists(account_id):
                self._account_table.item(account_id, values=values)
            else:
                self._account_table.insert(
                    "", "end", iid=account_id, values=values
                )
        self._update_delete_account_button()

    def _update_delete_account_button(self) -> None:
        can_delete = (
            self._controller.is_serving
            and len(self._account_table.selection()) == 1
        )
        self._delete_account_button.configure(
            state="normal" if can_delete else "disabled"
        )

    def _refresh_online_user_table(self) -> None:
        """Redraw one row for every authenticated WebSocket connection."""
        rows = {
            user.connection_id: (
                user.connection_id,
                user.account_id,
                user.nickname,
            )
            for user in self._controller.get_online_users()
        }

        for connection_id in set(self._online_user_table.get_children()) - set(rows):
            self._online_user_table.delete(connection_id)
        for connection_id, values in rows.items():
            if self._online_user_table.exists(connection_id):
                self._online_user_table.item(connection_id, values=values)
            else:
                self._online_user_table.insert(
                    "", "end", iid=connection_id, values=values
                )

    def _on_state_change(self, previous: str, current: str) -> None:
        if current == STATE_RUNNING:
            config = self._controller.config
            logger.info("Server started")
            if config is not None:
                logger.info("Listening on %s", config.address)
                logger.info("Board size: %s", config.board_label)
        elif current == STATE_STOPPED and previous != STATE_STOPPED:
            error = self._controller.error
            if error is not None:
                messagebox.showerror("Server error", str(error))

    def _apply_state(self, state: str) -> None:
        """Enable/disable widgets so settings can only change while stopped."""
        self._state = state
        self._status_var.set(state)
        self._status_label.configure(foreground=STATE_COLORS[state])

        stopped = state == STATE_STOPPED
        input_state = "normal" if stopped else "disabled"
        self._host_entry.configure(state=input_state)
        self._port_entry.configure(state=input_state)
        for button in self._board_buttons:
            button.configure(state=input_state)
        self._start_button.configure(state="normal" if stopped else "disabled")
        self._stop_button.configure(state="disabled" if stopped else "normal")


def install_gui_log_handler(
    log_queue: "queue.Queue[str]", level: int = logging.INFO
) -> QueueLogHandler:
    """Route the root logger into ``log_queue`` (in addition to the console)."""
    handler = QueueLogHandler(log_queue, level=level)
    handler.setFormatter(logging.Formatter(GUI_LOG_FORMAT, GUI_LOG_DATE_FORMAT))
    logging.getLogger().addHandler(handler)
    return handler


def run_gui(config: Optional[ServerConfig] = None) -> None:
    """Entry point used by ``python server.py``: show the management window."""
    configure_logging()
    log_queue: "queue.Queue[str]" = queue.Queue(maxsize=5000)
    handler = install_gui_log_handler(log_queue)
    controller = ServerController()

    root = tk.Tk()
    ServerGUI(root, controller, log_queue, config)
    try:
        root.mainloop()
    finally:
        # Whatever happens, do not leave a server thread behind.
        controller.stop()
        logging.getLogger().removeHandler(handler)
