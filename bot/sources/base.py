from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sqlalchemy.orm import Session


@dataclass
class SyncResult:
    source: str
    matches_upserted: int = 0
    players_upserted: int = 0
    tournaments_upserted: int = 0
    sets_written: int = 0
    skipped_files: int = 0
    errors: list[str] = field(default_factory=list)


class TennisDataSource(ABC):
    """A source of completed results (and optionally upcoming schedule).

    Sources never provide in-play state — that is exclusively the market
    estimator's job (Phase 3.5).
    """

    name: str

    @abstractmethod
    def sync(self, db: Session, *, full: bool = False) -> SyncResult:
        """Idempotent upsert of this source's data into the canonical tables."""
