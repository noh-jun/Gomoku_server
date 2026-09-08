"""Game kinds supported by the lobby and room factory."""

from enum import Enum


class GameType(str, Enum):
    GOMOKU = "GOMOKU"
    OTHELLO = "OTHELLO"


SUPPORTED_GAME_TYPES: tuple[GameType, ...] = tuple(GameType)
