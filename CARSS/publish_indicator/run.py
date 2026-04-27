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

    from scraper.sites.carss_indicator.scrape import run

    p = argparse.ArgumentParser(description="CARSS publish/indicator scraper (journal entrypoint).")
    p.add_argument("--cookies", required=True, help="Path to exported data.carss.cn cookies JSON.")
    p.add_argument(
        "--out",
        default=os.path.join("artifacts", "carss", "publish_indicator", "carss_drug_resistance_lineplot.csv"),
        help="Output CSV path.",
    )
    p.add_argument("--year", type=int, default=0, help="Optional: request year (default from config).")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--max-attempts", type=int, default=6)
    p.add_argument("--sleep", type=float, default=0.5)
    p.add_argument("--max-sleep", type=float, default=10.0)
    p.add_argument("--limit-combos", type=int, default=0, help="Debug: limit combos.")
    p.add_argument("--append", action="store_true", help="Append to output if it exists (resume).")
    p.add_argument("--playwright", action="store_true", help="Fetch API via Playwright (WAF bypass).")
    p.add_argument("--area-codes", default="", help="Optional: comma-separated areaCodes.")
    p.add_argument("--options", default="", help="Override layers options JSON path.")
    p.add_argument("--indicators", default="", help="Override indicator options JSON path.")
    args = p.parse_args(argv)

    extra: list[str] = []
    if args.limit_combos:
        extra += ["--limit-combos", str(args.limit_combos)]
    if args.append:
        extra += ["--append"]
    if args.playwright:
        extra += ["--playwright"]
    if args.area_codes:
        extra += ["--area-codes", args.area_codes]
    if args.options:
        extra += ["--options", args.options]
    if args.indicators:
        extra += ["--indicators", args.indicators]

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

