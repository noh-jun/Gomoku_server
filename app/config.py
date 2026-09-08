"""Server side configuration.

The server - not the client - decides the board geometry.  A
:class:`ServerConfig` is produced once (by the GUI or the CLI) and handed to
the application; from there the immutable :class:`GameSettings` travel down
to every room and game.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Mapping, Optional

from .board import Color
from .game_type import GameType

DEFAULT_HOST: Final[str] = "0.0.0.0"
DEFAULT_PORT: Final[int] = 8000
DEFAULT_BOARD_SIZE: Final[int] = 15
DEFAULT_WIN_LENGTH: Final[int] = 5

#: The color that opens a game and carries the Renju forbidden moves.
DEFAULT_STARTING_COLOR: Final[Color] = Color.WHITE

#: Name of the rule engine used by default (see :mod:`app.rules`).
RULE_NAME: Final[str] = "renju"

#: Board sizes the server offers.  The game logic itself is size agnostic.
SUPPORTED_BOARD_SIZES: Final[tuple[int, ...]] = (15, 19)

#: Smallest sensible row length; anything below is not a Gomoku game.
MIN_WIN_LENGTH: Final[int] = 2
OTHELLO_BOARD_SIZE: Final[int] = 8
OTHELLO_STARTING_COLOR: Final[Color] = Color.BLACK
SUPPORTED_TURN_TIME_LIMITS: Final[tuple[int, ...]] = (5, 10, 15, 30, 60)


def _application_root() -> Path:
    """Return the source root, or the executable directory when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


DEFAULT_ACCOUNT_DB_PATH: Final[Path] = _application_root() / "data" / "accounts.db"


class ConfigError(ValueError):
    """Raised for an invalid server configuration (bad port, board size...)."""


@dataclass(frozen=True)
class GameSettings:
    """Geometry and win condition of a single game.

    Frozen on purpose: a room copies these values when it is created and
    keeps them for its whole lifetime, so changing the server configuration
    can never resize a running game.
    """

    board_size: int = DEFAULT_BOARD_SIZE
    win_length: Optional[int] = DEFAULT_WIN_LENGTH
    starting_color: Color = DEFAULT_STARTING_COLOR
    game_type: GameType = GameType.GOMOKU
    turn_time_limit_sec: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.game_type, GameType):
            raise ConfigError("game_type must be a GameType.")
        if not isinstance(self.starting_color, Color):
            raise ConfigError("starting_color must be a Color.")
        if (
            self.turn_time_limit_sec is not None
            and self.turn_time_limit_sec not in SUPPORTED_TURN_TIME_LIMITS
        ):
            raise ConfigError(
                "turn_time_limit_sec must be 5, 10, 15, 30, 60, or null."
            )
        if self.game_type is GameType.OTHELLO:
            if self.board_size != OTHELLO_BOARD_SIZE:
                raise ConfigError("Othello board_size must be 8.")
            if self.win_length is not None:
                raise ConfigError("Othello win_length must be null.")
            if self.starting_color is not OTHELLO_STARTING_COLOR:
                raise ConfigError("Othello starting_color must be BLACK.")
            return
        if self.board_size < MIN_WIN_LENGTH:
            raise ConfigError(f"board_size must be >= {MIN_WIN_LENGTH}.")
        if (
            self.win_length is None
            or not MIN_WIN_LENGTH <= self.win_length <= self.board_size
        ):
            raise ConfigError(
                f"win_length must be between {MIN_WIN_LENGTH} and the board size."
            )

    @property
    def board_label(self) -> str:
        """``"19 x 19"`` - used in logs and in the GUI."""
        return f"{self.board_size} x {self.board_size}"


@dataclass(frozen=True)
class ServerConfig:
    """Everything the operator can choose before starting the server."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    board_size: int = DEFAULT_BOARD_SIZE
    win_length: int = DEFAULT_WIN_LENGTH
    starting_color: Color = DEFAULT_STARTING_COLOR
    account_db_path: Path = DEFAULT_ACCOUNT_DB_PATH

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ConfigError("host must not be empty.")
        if not 1 <= self.port <= 65535:
            raise ConfigError("port must be between 1 and 65535.")
        if self.board_size not in SUPPORTED_BOARD_SIZES:
            supported = " / ".join(str(size) for size in SUPPORTED_BOARD_SIZES)
            raise ConfigError(f"board_size must be one of: {supported}.")
        # Reuse the game level validation for the win condition.
        GameSettings(
            board_size=self.board_size,
            win_length=self.win_length,
            starting_color=self.starting_color,
        )

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "ServerConfig":
        """Read the configuration from ``GOMOKU_*`` environment variables.

        Used by the module level ``app.main:app`` object, which cannot take
        constructor arguments (``uvicorn app.main:app``, ``--reload``).
        """
        source = env if env is not None else os.environ

        def read_int(name: str, default: int) -> int:
            raw = source.get(name)
            if raw is None or not raw.strip():
                return default
            try:
                return int(raw)
            except ValueError:
                raise ConfigError(f"{name} must be an integer, got {raw!r}.")

        raw_color = source.get("GOMOKU_STARTING_COLOR")
        if raw_color is None or not raw_color.strip():
            starting_color = DEFAULT_STARTING_COLOR
        else:
            try:
                starting_color = Color(raw_color.strip().upper())
            except ValueError:
                raise ConfigError(
                    f"GOMOKU_STARTING_COLOR must be BLACK or WHITE, got {raw_color!r}."
                )

        return cls(
            host=source.get("GOMOKU_HOST", DEFAULT_HOST),
            port=read_int("GOMOKU_PORT", DEFAULT_PORT),
            board_size=read_int("GOMOKU_BOARD_SIZE", DEFAULT_BOARD_SIZE),
            win_length=read_int("GOMOKU_WIN_LENGTH", DEFAULT_WIN_LENGTH),
            starting_color=starting_color,
            account_db_path=Path(
                source.get("OMOK_ACCOUNT_DB_PATH", str(DEFAULT_ACCOUNT_DB_PATH))
            ),
        )

    def as_env(self) -> dict[str, str]:
        """The inverse of :meth:`from_env`, for spawning reload workers."""
        return {
            "GOMOKU_HOST": self.host,
            "GOMOKU_PORT": str(self.port),
            "GOMOKU_BOARD_SIZE": str(self.board_size),
            "GOMOKU_WIN_LENGTH": str(self.win_length),
            "GOMOKU_STARTING_COLOR": self.starting_color.value,
            "OMOK_ACCOUNT_DB_PATH": str(self.account_db_path),
        }

    @property
    def game_settings(self) -> GameSettings:
        """The subset that rooms and games actually need."""
        return GameSettings(
            board_size=self.board_size,
            win_length=self.win_length,
            starting_color=self.starting_color,
        )

    def settings_for(self, game_type: GameType) -> GameSettings:
        """Return immutable settings for a newly created game room."""
        if game_type is GameType.OTHELLO:
            return GameSettings(
                game_type=GameType.OTHELLO,
                board_size=OTHELLO_BOARD_SIZE,
                win_length=None,
                starting_color=OTHELLO_STARTING_COLOR,
            )
        return self.game_settings

    @property
    def board_label(self) -> str:
        return self.game_settings.board_label

    @property
    def rule_name(self) -> str:
        """Rule engine in use; shown in the GUI and on the HTTP index."""
        return RULE_NAME

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"
