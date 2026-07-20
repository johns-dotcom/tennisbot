"""Walk-forward backtest: Brier score + calibration curve by probability bucket.

For every played match inside [date_from, date_to): predict pre-match (state 0-0)
using only ratings accumulated from matches strictly earlier in the walk order,
score the prediction, then feed the match to the model. No lookahead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from bot.log import get_logger
from bot.prob.elo import SetElo
from bot.prob.model import MatchState

log = get_logger("prob.backtest")

MIN_CONFIDENCE_SCORED = 0.25  # skip scoring when either player is nearly unrated


@dataclass
class BacktestReport:
    date_from: date
    date_to: date
    n_scored: int = 0
    n_skipped_low_confidence: int = 0
    brier: float = 0.0
    log_loss: float = 0.0
    buckets: list[dict] = field(default_factory=list)  # [{lo, hi, n, mean_p, win_rate}]

    def render(self) -> str:
        lines = [
            f"BACKTEST {self.date_from} → {self.date_to}",
            f"  scored: {self.n_scored}   skipped (confidence < {MIN_CONFIDENCE_SCORED}): "
            f"{self.n_skipped_low_confidence}",
            f"  Brier score: {self.brier:.4f}   log loss: {self.log_loss:.4f}",
            "",
            "  CALIBRATION (predicted → observed)",
            "  bucket      n      mean_p   win_rate   gap",
        ]
        for b in self.buckets:
            gap = b["win_rate"] - b["mean_p"]
            lines.append(f"  {b['lo']:.1f}-{b['hi']:.1f}  {b['n']:7d}   {b['mean_p']:.3f}"
                         f"    {b['win_rate']:.3f}   {gap:+.3f}")
        return "\n".join(lines)


def run_backtest(db: Session, date_from: date, date_to: date) -> BacktestReport:
    import math

    model = SetElo()
    rows = model._load_matches(db, through=date_to)
    report = BacktestReport(date_from=date_from, date_to=date_to)
    preds: list[tuple[float, bool]] = []

    for row in rows:
        in_window = row["date"] >= date_from
        if in_window:
            # predict the match winner's side as "player A" is a labeling trick
            # that would leak the answer — predict for (winner, loser) but score
            # p as P(actual winner wins), which is label-symmetric.
            state = MatchState(0, 0, row["best_of"] if row["best_of"] in (3, 5) else 3)
            pred = model.predict(row["winner_id"], row["loser_id"], row["surface"],
                                 row["tier"], state)
            if pred.confidence >= MIN_CONFIDENCE_SCORED:
                preds.append((pred.p_a, True))
            else:
                report.n_skipped_low_confidence += 1
        model.apply_match(row["winner_id"], row["loser_id"], row["surface"],
                          row["tier"], row["set_results"])

    if preds:
        report.n_scored = len(preds)
        report.brier = sum((p - 1.0) ** 2 for p, _ in preds) / len(preds)
        report.log_loss = -sum(math.log(max(p, 1e-12)) for p, _ in preds) / len(preds)
        edges = [i / 10 for i in range(11)]
        # mirror each prediction with its complementary loser-side view so the
        # calibration curve covers both tails symmetrically
        both = [(p, 1.0) for p, _ in preds] + [(1 - p, 0.0) for p, _ in preds]
        for lo, hi in zip(edges, edges[1:]):
            inb = [(p, y) for p, y in both if lo <= p < hi or (hi == 1.0 and p == 1.0)]
            if inb:
                report.buckets.append({
                    "lo": lo, "hi": hi, "n": len(inb),
                    "mean_p": sum(p for p, _ in inb) / len(inb),
                    "win_rate": sum(y for _, y in inb) / len(inb),
                })
    return report
