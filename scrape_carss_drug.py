from __future__ import annotations

import sys

from scraper.sites.carss_drug.scrape import run


def main(argv: list[str]) -> int:
    # Backward-compatible wrapper around the new plugin.
    # Keep CLI flags stable for existing workflows.
    out = ""
    cookies = None
    timeout = 30.0
    max_attempts = 6
    sleep = 0.5
    max_sleep = 10.0
    extra_args = argv
    return run(
        out=out,
        cookies=cookies,
        timeout=timeout,
        max_attempts=max_attempts,
        sleep=sleep,
        max_sleep=max_sleep,
        year=0,
        extra_args=extra_args,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

