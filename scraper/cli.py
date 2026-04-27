from __future__ import annotations

import argparse
import importlib
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class SiteSpec:
    name: str
    module: str


SITES: dict[str, SiteSpec] = {
    "carss_drug": SiteSpec(name="carss_drug", module="scraper.sites.carss_drug.scrape"),
    "carss_indicator": SiteSpec(name="carss_indicator", module="scraper.sites.carss_indicator.scrape"),
    "stats_gov_cn": SiteSpec(name="stats_gov_cn", module="scraper.sites.stats_gov_cn.scrape"),
}


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    p = argparse.ArgumentParser(prog="scraper", description="Reusable scraping toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run a site scraper")
    run.add_argument("site", choices=sorted(SITES.keys()))
    run.add_argument("--out", default="", help="Output path (site-specific default if empty).")

    # Common options that site modules may use
    run.add_argument("--cookies", default=None, help="Optional cookies JSON (name->value).")
    run.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout seconds.")
    run.add_argument("--max-attempts", type=int, default=6, help="Max attempts per request.")
    run.add_argument("--sleep", type=float, default=0.5, help="Initial backoff seconds.")
    run.add_argument("--max-sleep", type=float, default=10.0, help="Max backoff seconds.")

    # Stats.gov.cn specifics
    run.add_argument("--year", type=int, default=0, help="Year for yearbook sites (e.g. 2024).")

    args, extra = p.parse_known_args(argv)

    if args.cmd == "run":
        spec = SITES[args.site]
        mod = importlib.import_module(spec.module)
        if not hasattr(mod, "run"):
            raise RuntimeError(f"Site module {spec.module} missing run()")
        return int(
            mod.run(
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

    raise RuntimeError("unreachable")

