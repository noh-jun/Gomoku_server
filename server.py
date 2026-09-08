"""Entrypoint: ``python server.py``.

By default a Tkinter management GUI opens, where the operator picks the host,
the port and the board size (15 x 15 or 19 x 19) and then starts the server.
``--no-gui`` runs uvicorn directly, which is handy for headless machines.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn

from app.config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_WIN_LENGTH,
    SUPPORTED_BOARD_SIZES,
    ConfigError,
    ServerConfig,
)
from app.log import configure_logging

logger = logging.getLogger("gomoku")

APP_IMPORT_PATH = "app.main:app"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real-time Gomoku server")
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help="bind address (GUI default)"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="bind port (GUI default)"
    )
    parser.add_argument(
        "--board-size",
        type=int,
        default=SUPPORTED_BOARD_SIZES[0],
        choices=SUPPORTED_BOARD_SIZES,
        help="board size (GUI default)",
    )
    parser.add_argument(
        "--win-length",
        type=int,
        default=DEFAULT_WIN_LENGTH,
        help="stones in a row needed to win",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="run the server directly, without the management window",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="auto reload on code changes (implies --no-gui)",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        help="uvicorn log level",
    )
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> ServerConfig:
    return ServerConfig(
        host=args.host,
        port=args.port,
        board_size=args.board_size,
        win_length=args.win_length,
    )


def run_headless(config: ServerConfig, log_level: str, reload: bool) -> int:
    """Run uvicorn in the foreground (no GUI)."""
    configure_logging(level=logging.INFO, force=True)
    logger.info("Board size: %s", config.board_label)
    logger.info("Listening on ws://%s/ws", config.address)

    if reload:
        # Reload workers re-import the app, so the config travels via env.
        os.environ.update(config.as_env())
        uvicorn.run(
            APP_IMPORT_PATH,
            host=config.host,
            port=config.port,
            reload=True,
            log_level=log_level,
        )
        return 0

    from app.main import create_app

    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        log_level=log_level,
    )
    return 0


def run_with_gui(config: ServerConfig) -> int:
    """Open the Tkinter management window."""
    try:
        import tkinter

        from gui.server_gui import run_gui
    except ImportError as exc:
        print(
            f"Tkinter is not available ({exc}). "
            f"Run the server without the GUI: python server.py --no-gui",
            file=sys.stderr,
        )
        return 1

    try:
        run_gui(config)
    except tkinter.TclError as exc:
        print(
            f"No display available for the GUI ({exc}). "
            f"Run the server without the GUI: python server.py --no-gui",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = build_config(args)
    except ConfigError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 2

    if args.no_gui or args.reload:
        return run_headless(config, args.log_level, args.reload)
    return run_with_gui(config)


if __name__ == "__main__":
    sys.exit(main())
