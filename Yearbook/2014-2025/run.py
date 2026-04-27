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

    from scraper.sites.stats_gov_cn.scrape import run

    p = argparse.ArgumentParser(description="stats.gov.cn yearbook runner (2014–2025: JPG tables).")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--out", default="", help="Output folder (default under artifacts/ if empty).")
    p.add_argument("--index", action="store_true", help="Write yearbook_index.csv.")
    p.add_argument("--download", action="store_true", help="Download JPG images.")
    p.add_argument("--download-workers", type=int, default=4, help="Parallel download workers.")
    p.add_argument("--chapter", default="", help="Optional chapter filter (e.g. 12).")
    p.add_argument("--limit", type=int, default=0, help="Debug: limit number of sections.")
    args = p.parse_args(argv)

    out_dir = (
        args.out
        or os.path.join("artifacts", "yearbook", "2014-2025", str(args.year))
    )

    extra: list[str] = []
    if args.index:
        extra += ["--index"]
    if args.download:
        extra += ["--download"]
    if args.download_workers:
        extra += ["--download-workers", str(args.download_workers)]
    if args.chapter:
        extra += ["--chapter", args.chapter]
    if args.limit:
        extra += ["--limit", str(args.limit)]

    return int(
        run(
            out=out_dir,
            cookies=None,
            timeout=30.0,
            max_attempts=6,
            sleep=0.5,
            max_sleep=10.0,
            year=args.year,
            extra_args=extra,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

