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

    if args.cmd == "profile":
        from datetime import date, timedelta

        from bot.db import session
        from bot.matching.market_matcher import PlayerMatcher
        from bot.stats.profile import build_profile
        from bot.stats.render import render_profile

        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today() + timedelta(days=1)
        with session() as db:
            matcher = PlayerMatcher(db)
            res = matcher.match(db, args.name, source="cli", queue_on_miss=False)
            if res.player_id is None:
                print(f"No confident match for '{args.name}'.", file=sys.stderr)
                return 1
            print(render_profile(build_profile(db, res.player_id, as_of)))
        return 0

    if args.cmd == "backtest":
        from datetime import date

        from bot.db import session
        from bot.prob.backtest import run_backtest

        with session() as db:
            report = run_backtest(db, date.fromisoformat(args.date_from),
                                  date.fromisoformat(args.date_to))
        print(report.render())
        return 0

    if args.cmd == "replay":
        from bot.db import session
        from bot.market.replay import replay_session

        with session() as db:
            print(replay_session(db, args.session_id).render())
        return 0

    if args.cmd == "inference-report":
        from bot.db import session
        from bot.reports import inference_report

        with session() as db:
            print(inference_report(db))
        return 0

    if args.cmd == "graduate":
        from bot.db import session
        from bot.reports import graduate_report

        with session() as db:
            text, ok = graduate_report(db)
        print(text)
        return 0 if ok else 1

    if args.cmd == "watch":
        from bot.watch import main as watch_main

        return watch_main()

    print(f"unknown command '{args.cmd}'", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
