from __future__ import annotations

import argparse
import os
import sys


def _ensure_src_on_path() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    src = os.path.join(repo_root, "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    _ensure_src_on_path()

    from scraper.sites.carss_drug.scrape import run

    p = argparse.ArgumentParser(description="CARSS publish/drug scraper (journal entrypoint).")
    p.add_argument("--cookies", required=True, help="Path to exported data.carss.cn cookies JSON.")
    p.add_argument(
        "--out",
        default=os.path.join("artifacts", "carss", "publish_drug", "carss_drug_resistance.csv"),
        help="Output CSV path.",
    )
    p.add_argument("--year", type=int, default=0, help="Optional: scrape a single year.")
    p.add_argument("--options", default="", help="Override options JSON path.")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--max-attempts", type=int, default=6)
    p.add_argument("--sleep", type=float, default=0.5)
    p.add_argument("--max-sleep", type=float, default=10.0)
    p.add_argument("--limit-combos", type=int, default=0, help="Debug: limit combos.")
    args = p.parse_args(argv)

    extra = []
    if args.limit_combos:
        extra += ["--limit-combos", str(args.limit_combos)]
    if args.options:
        extra += ["--options", args.options]

    return int(
        run(
            out=args.out,
            cookies=args.cookies,
            timeout=args.timeout,
            max_attempts=args.max_attempts,
            sleep=args.sleep,
            max_sleep=args.max_sleep,
            year=args.year,
            extra_args=extra,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

