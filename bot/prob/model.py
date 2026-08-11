"""WinProbabilityModel interface.

CIRCULARITY RULE (CLAUDE.md rule 2): market price NEVER reaches this engine.
Nothing in bot/prob may import from bot/market or accept price/odds inputs.
Market data may inform exactly one thing: the discrete MatchState (sets won),
which the estimator derives elsewhere. tests/test_isolation.py enforces this
structurally — do not weaken it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class MatchState:
    """Discrete in-play state: completed sets won per side. Nothing else."""

    sets_a: int = 0
    sets_b: int = 0
    best_of: int = 3

    def __post_init__(self) -> None:
        need = self.best_of // 2 + 1
        if not (0 <= self.sets_a < need and 0 <= self.sets_b < need):
            raise ValueError(f"impossible state {self.sets_a}-{self.sets_b} in Bo{self.best_of}")

    @property
    def key(self) -> str:
        return f"{self.sets_a}-{self.sets_b}"


@dataclass(frozen=True)
class Prediction:
    p_a: float  # P(player A wins the match), conditioned on state
    confidence: float  # 0-1, data-quality confidence in the estimate


class WinProbabilityModel(ABC):
    """Implementations estimate P(A beats B) from the bot's OWN data only."""

    @abstractmethod
    def predict(self, player_a: int, player_b: int, surface: str | None,
                tier: str | None, match_state: MatchState,
                as_of: date | None = None) -> Prediction:
        """as_of: the prediction date. When given, recency-based confidence is
        decayed forward to that date (so a layoff since a player's last match
        lowers confidence at prediction time, not one match too late)."""
        ...
