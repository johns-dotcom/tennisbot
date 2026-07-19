"""CLI: python -m bot <command>

ingest | watch | profile "Name" | backtest --from --to | replay <session> |
inference-report | graduate
"""
import argparse
import sys

from bot.log import setup_logging


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(prog="bot", description="Tennis advisory bot (never trades)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="Sackmann + api-tennis sync, dedup, stats refresh")
    p_ingest.add_argument("--full", action="store_true", help="ignore incremental watermarks")
    p_ingest.add_argument("--skip-live", action="store_true", help="Sackmann only")

    p_profile = sub.add_parser("profile", help="print a player's play script")
    p_profile.add_argument("name")
    p_profile.add_argument("--as-of", default=None)

    p_backtest = sub.add_parser("backtest")
    p_backtest.add_argument("--from", dest="date_from", required=True)
    p_backtest.add_argument("--to", dest="date_to", required=True)

    sub.add_parser("watch", help="live loop: markets, estimator, advisories")

    p_replay = sub.add_parser("replay", help="replay a recorded market session")
    p_replay.add_argument("session_id")

    sub.add_parser("inference-report", help="estimator accuracy vs delayed scores")
    sub.add_parser("graduate", help="check probation graduation thresholds")

    args = parser.parse_args()

    if args.cmd == "ingest":
        from bot.db import session
        from bot.ingest.pipeline import run_ingest

        with session() as db:
            results = run_ingest(db, full=args.full, skip_live=args.skip_live)
        return 1 if any(r.errors for r in results) else 0

    phase = {"profile": 2, "backtest": 3, "replay": 3.5, "inference-report": 3.5,
             "watch": 4, "graduate": 5}.get(args.cmd)
    print(f"'{args.cmd}' is implemented in Phase {phase} — not built yet.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
