from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable

from ...http import RetryConfig, load_cookies_json, make_session, request_json_with_retries


API_URL = "https://data.carss.cn/Api/Drug/GetDrrData"
REFERER = "https://data.carss.cn/publish/drug"
ORIGIN = "https://data.carss.cn"

CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class Layer:
    layerCode: str
    name: str


@dataclass(frozen=True)
class Organism:
    organism: str
    name: str


def load_options(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_DRUG_FULL_RE = re.compile(r"^(?P<cn>.+?)\((?P<en>[^()]*)\)\s*$")


def split_drug_full_name(drug_full_name: str | None, drug_name_fallback: str | None) -> tuple[str, str]:
    s = (drug_full_name or "").strip()
    if s:
        m = _DRUG_FULL_RE.match(s)
        if m:
            return (m.group("cn").strip(), m.group("en").strip())
        return (s, "")
    return ((drug_name_fallback or "").strip(), "")


def iter_combos(years: Iterable[int], layers: list[Layer], organisms: list[Organism]):
    for y in years:
        for layer in layers:
            for org in organisms:
                yield (y, layer, org)


def normalize_rows(
    payload_year: int,
    layer: Layer,
    organism: Organism,
    api_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in api_data:
        drug_code = str(item.get("drugCode") or "").strip()
        drug_name = str(item.get("drugName") or "").strip()
        drug_full = item.get("drugFullName")
        drug_cn, drug_en = split_drug_full_name(
            str(drug_full) if drug_full is not None else None,
            drug_name,
        )

        year_val = int(item.get("year") or payload_year)
        bacteria_species = (item.get("fullName") or item.get("name") or organism.name)
        bacteria_species = str(bacteria_species).strip()

        total_n = item.get("validTotal", item.get("total"))
        try:
            total_n_int = int(total_n) if total_n is not None else 0
        except Exception:
            total_n_int = 0

        def pct(rate_key: str) -> float:
            v = item.get(rate_key)
            try:
                return float(v) * 100.0 if v is not None else 0.0
            except Exception:
                return 0.0

        def cnt(cnt_key: str) -> int:
            v = item.get(cnt_key)
            try:
                return int(v) if v is not None else 0
            except Exception:
                return 0

        out.append(
            {
                "year": year_val,
                "province": layer.name,
                "layer_code": layer.layerCode,
                "bacteria_species": bacteria_species,
                "organism_code": organism.organism,
                "drug_code": drug_code,
                "drug_full_name_cn": drug_cn,
                "drug_full_name_en": drug_en,
                "total_n_strains": total_n_int,
                "resistant_percent": pct("rRate"),
                "resistant_n_strains": cnt("rTotal"),
                "intermediate_percent": pct("iRate"),
                "intermediate_n_strains": cnt("iTotal"),
                "sensitive_percent": pct("sRate"),
                "sensitive_n_strains": cnt("sTotal"),
            }
        )
    return out


def run(
    *,
    out: str,
    cookies: str | None,
    timeout: float,
    max_attempts: int,
    sleep: float,
    max_sleep: float,
    year: int = 0,
    extra_args: list[str] | None = None,
) -> int:
    extra_args = extra_args or []
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument(
        "--options",
        default="CARSS/publish_indicator/configs/layers_options.json",
        help="CARSS publish/drug layer+organism options (JSON).",
    )
    ap.add_argument("--limit-combos", type=int, default=0)
    ns, _ = ap.parse_known_args(extra_args)

    opts = load_options(ns.options)
    years = [int(y) for y in opts["years"]]
    layers = [Layer(layerCode=str(x["layerCode"]), name=str(x["name"])) for x in opts["layers"]]
    organisms = [Organism(organism=str(x["organism"]), name=str(x["name"])) for x in opts["organisms"]]
    defaults = dict(opts.get("request_defaults") or {})

    if year:
        years = [int(year)]

    retry = RetryConfig(max_attempts=max_attempts, min_sleep_s=sleep, max_sleep_s=max_sleep)

    sess = make_session(
        timeout_s=timeout,
        user_agent=CHROME_UA,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": ORIGIN,
            "Referer": REFERER,
            "apptype": "publish",
        },
    )
    if cookies:
        load_cookies_json(sess, cookies)

    out_path = out or "carss_drug_resistance.csv"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    fieldnames = [
        "year",
        "province",
        "layer_code",
        "bacteria_species",
        "organism_code",
        "drug_code",
        "drug_full_name_cn",
        "drug_full_name_en",
        "total_n_strains",
        "resistant_percent",
        "resistant_n_strains",
        "intermediate_percent",
        "intermediate_n_strains",
        "sensitive_percent",
        "sensitive_n_strains",
    ]

    combo_iter = iter_combos(years, layers, organisms)
    written_rows = 0
    combo_count = 0

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()

        for (y, layer, org) in combo_iter:
            combo_count += 1
            payload: dict[str, Any] = {
                "year": str(y),
                "layerCode": layer.layerCode,
                "organism": org.organism,
                **defaults,
            }

            try:
                data = request_json_with_retries(sess, "POST", API_URL, retry=retry, json=payload)
            except RuntimeError as e:
                msg = str(e)
                if "HTTP 468" in msg:
                    raise RuntimeError(
                        "CARSS returned HTTP 468 (WAF challenge). Your cookies likely expired.\n"
                        "Export fresh `data.carss.cn` cookies from your browser and re-run with `--cookies <file>`."
                    ) from e
                raise
            if not data.get("success") or str(data.get("code")) != "200":
                raise RuntimeError(f"Unexpected API result for payload={payload}: {str(data)[:400]}")

            rows = normalize_rows(y, layer, org, list(data.get("data") or []))
            for row in rows:
                w.writerow(row)
            written_rows += len(rows)

            if combo_count % 25 == 0:
                print(f"[carss_drug] combos={combo_count} rows={written_rows}", file=sys.stderr)

            if ns.limit_combos and combo_count >= ns.limit_combos:
                break

    print(f"[carss_drug] done combos={combo_count} rows={written_rows} -> {out_path}", file=sys.stderr)
    return 0

