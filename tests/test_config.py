"""Tests for the server configuration objects."""

from __future__ import annotations

import pytest

from app.board import Color
from app.config import (
    DEFAULT_BOARD_SIZE,
    DEFAULT_STARTING_COLOR,
    DEFAULT_PORT,
    DEFAULT_WIN_LENGTH,
    RULE_NAME,
    SUPPORTED_BOARD_SIZES,
    ConfigError,
    GameSettings,
    ServerConfig,
)


def test_defaults() -> None:
    config = ServerConfig()

    assert config.host == "0.0.0.0"
    assert config.port == DEFAULT_PORT
    assert config.board_size == DEFAULT_BOARD_SIZE
    assert config.win_length == DEFAULT_WIN_LENGTH
    assert config.game_settings == GameSettings(
        board_size=DEFAULT_BOARD_SIZE, win_length=DEFAULT_WIN_LENGTH
    )


@pytest.mark.parametrize("board_size", SUPPORTED_BOARD_SIZES)
def test_supported_board_sizes(board_size: int) -> None:
    config = ServerConfig(board_size=board_size)

    assert config.board_size == board_size
    assert config.board_label == f"{board_size} x {board_size}"
    assert config.game_settings.board_size == board_size


@pytest.mark.parametrize("board_size", [0, 1, 14, 16, 20, -19])
def test_unsupported_board_size_is_rejected(board_size: int) -> None:
    with pytest.raises(ConfigError):
        ServerConfig(board_size=board_size)


@pytest.mark.parametrize("port", [0, -1, 65536, 999999])
def test_invalid_port_is_rejected(port: int) -> None:
    with pytest.raises(ConfigError):
        ServerConfig(port=port)


def test_empty_host_is_rejected() -> None:
    with pytest.raises(ConfigError):
        ServerConfig(host="   ")


def test_win_length_must_fit_on_the_board() -> None:
    with pytest.raises(ConfigError):
        GameSettings(board_size=4, win_length=5)
    with pytest.raises(ConfigError):
        GameSettings(board_size=15, win_length=1)

    # Five in a row fits on both offered sizes.
    assert GameSettings(board_size=15, win_length=5).win_length == 5
    assert GameSettings(board_size=19, win_length=5).win_length == 5


def test_settings_are_immutable() -> None:
    settings = GameSettings(board_size=19)

    with pytest.raises(Exception):
        settings.board_size = 15  # type: ignore[misc]


def test_address_and_labels() -> None:
    config = ServerConfig(host="127.0.0.1", port=9000, board_size=19)

    assert config.address == "127.0.0.1:9000"
    assert config.board_label == "19 x 19"


def test_from_env_reads_the_gomoku_variables() -> None:
    config = ServerConfig.from_env(
        {
            "GOMOKU_HOST": "127.0.0.1",
            "GOMOKU_PORT": "9100",
            "GOMOKU_BOARD_SIZE": "19",
        }
    )

    assert config.host == "127.0.0.1"
    assert config.port == 9100
    assert config.board_size == 19
    assert config.win_length == DEFAULT_WIN_LENGTH


def test_from_env_falls_back_to_defaults() -> None:
    config = ServerConfig.from_env({})

    assert config == ServerConfig()


def test_from_env_rejects_garbage() -> None:
    with pytest.raises(ConfigError):
        ServerConfig.from_env({"GOMOKU_PORT": "eight thousand"})


def test_as_env_round_trip() -> None:
    config = ServerConfig(host="127.0.0.1", port=9100, board_size=19)

    assert ServerConfig.from_env(config.as_env()) == config


# ----------------------------------------------------------------------
# starting color (WHITE opens and carries the Renju forbidden moves)
# ----------------------------------------------------------------------
def test_default_starting_color_is_white() -> None:
    config = ServerConfig()

    assert config.starting_color is Color.WHITE
    assert config.starting_color is DEFAULT_STARTING_COLOR
    assert config.game_settings.starting_color is Color.WHITE
    assert config.rule_name == RULE_NAME == "renju"


def test_starting_color_can_be_configured() -> None:
    config = ServerConfig(starting_color=Color.BLACK)

    assert config.game_settings.starting_color is Color.BLACK


def test_invalid_starting_color_is_rejected() -> None:
    with pytest.raises(ConfigError):
        ServerConfig(starting_color="WHITE")  # type: ignore[arg-type]


def test_settings_default_starting_color() -> None:
    assert GameSettings().starting_color is Color.WHITE


def test_from_env_reads_the_starting_color() -> None:
    config = ServerConfig.from_env({"GOMOKU_STARTING_COLOR": "black"})

    assert config.starting_color is Color.BLACK


def test_from_env_rejects_an_unknown_starting_color() -> None:
    with pytest.raises(ConfigError):
        ServerConfig.from_env({"GOMOKU_STARTING_COLOR": "GREEN"})


def test_as_env_includes_the_starting_color() -> None:
    config = ServerConfig(board_size=19, starting_color=Color.BLACK)

    assert config.as_env()["GOMOKU_STARTING_COLOR"] == "BLACK"
    assert ServerConfig.from_env(config.as_env()) == config
