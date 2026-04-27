from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from ...http import RetryConfig, load_cookies_json, make_session, request_json_with_retries

from .playwright_scrape import discover_options_via_ui, post_indicator_getall_via_browser


API_URL = "https://data.carss.cn/Api/Indicator/GetAll"
REFERER = "https://data.carss.cn/publish/indicator"
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
class Indicator:
    indicator: str
    name: str


def load_options(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _area_codes_to_layer_codes(layers: list[Layer]) -> dict[str, list[str]]:
    """
    Build a best-effort areaCode->layerCode[] mapping.

    Observed examples:
    - areaCode '00': ['00'] (national)
    - areaCode '10': ['00','0031'..'0037'] (East China)

    Without an official API for area groups, we provide a conservative default:
    - '00' -> ['00']
    - any other areaCode -> ['00'] + all province layer codes present in options.json (excluding '00')
    """
    all_codes = [l.layerCode for l in layers if l.layerCode]
    prov_codes = [c for c in all_codes if c != "00"]
    out: dict[str, list[str]] = {"00": ["00"]}
    for ac in [f"{i:02d}" for i in range(1, 100)] + [str(i) for i in range(1, 100)]:
        if ac == "00":
            continue
        out[ac] = ["00", *prov_codes]
    return out


def _parse_year_data_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    yd = (data or {}).get("yearData")
    if not isinstance(yd, list):
        return []
    items: list[dict[str, Any]] = []
    for grp in yd:
        its = grp.get("items") if isinstance(grp, dict) else None
        if isinstance(its, list):
            for it in its:
                if isinstance(it, dict):
                    items.append(it)
    return items


def _indicator_label_from_item(it: dict[str, Any], fallback: str) -> str:
    # Prefer the Chinese label used in the UI.
    for k in ("name", "fullName", "code"):
        v = it.get(k)
        if v is not None:
            s = str(v).strip()
            if s:
                return s
    return fallback


def _round_resistant_n(total_n: int, resistant_percent: float) -> int:
    # Choose a deterministic rounding rule for computed counts.
    # Use standard rounding to nearest int.
    return int(round(float(total_n) * float(resistant_percent) / 100.0))


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
    ap.add_argument("--options", default="inputs/options.json", help="Reuse layer code->name mapping.")
    ap.add_argument("--indicators", default="inputs/indicator_options.json", help="Indicator list (code->label).")
    ap.add_argument("--area-codes", default="", help="Comma-separated areaCodes to run (default: all in file).")
    ap.add_argument("--limit-combos", type=int, default=0, help="Limit areaCode×indicator combos (debug).")
    ap.add_argument("--append", action="store_true", help="Append to output if it exists (resume).")
    ap.add_argument("--discover", action="store_true", help="Discover all dropdown options via Playwright UI.")
    ap.add_argument("--playwright", action="store_true", help="Fetch API via Playwright (bypass WAF 468).")
    ns, _ = ap.parse_known_args(extra_args)

    layer_opts = load_options(ns.options)
    layers = [Layer(layerCode=str(x["layerCode"]), name=str(x["name"])) for x in layer_opts["layers"]]
    layer_by_code = {l.layerCode: l for l in layers}

    ind_opts = load_options(ns.indicators)
    indicators = [Indicator(indicator=str(x["indicator"]), name=str(x["name"])) for x in ind_opts["indicators"]]

    if ns.discover:
        areas_ui, inds_ui = discover_options_via_ui(cookies_path=cookies or "cookies.json")
        # Replace indicators with UI-discovered
        indicators = [Indicator(indicator=i.indicator, name=i.label) for i in inds_ui]
        # Replace areaCodes + mapping with UI-discovered
        ind_opts["areaCodes"] = [a.areaCode for a in areas_ui]
        ind_opts["areaCodeToLayerCodes"] = {a.areaCode: a.layerCodes for a in areas_ui}

    defaults = dict(ind_opts.get("request_defaults") or {})
    years = [int(y) for y in ind_opts.get("years") or []]
    if year:
        years = [int(year)]
    # For this endpoint, one call returns multiple years in yearData.
    # If years is empty, default to latest known year in options.
    request_year = max(years) if years else (int(year) if year else 2024)

    # areaCode mapping
    area_codes = ind_opts.get("areaCodes")
    if not isinstance(area_codes, list) or not area_codes:
        area_codes = ["00"]

    if ns.area_codes:
        area_codes = [x.strip() for x in ns.area_codes.split(",") if x.strip()]

    area_to_layers = ind_opts.get("areaCodeToLayerCodes")
    if not isinstance(area_to_layers, dict) or not area_to_layers:
        area_to_layers = _area_codes_to_layer_codes(layers)

    retry = RetryConfig(max_attempts=max_attempts, min_sleep_s=sleep, max_sleep_s=max_sleep)
    sess = None
    if not ns.playwright:
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

    out_path = out or "carss_drug_resistance_lineplot.csv"
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

    mode = "a" if ns.append and os.path.exists(out_path) else "w"
    write_header = mode == "w"

    errors_path = os.path.join(os.path.dirname(out_path) or ".", "carss_indicator_errors.jsonl")
    errors_mode = "a"

    written = 0
    combo_count = 0

    with open(out_path, mode, newline="", encoding="utf-8") as f, open(
        errors_path, errors_mode, encoding="utf-8"
    ) as ef:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()

        for ac in area_codes:
            layer_codes = area_to_layers.get(ac) or ["00"]
            # Ensure stable ordering and uniqueness
            seen = set()
            layer_codes = [c for c in layer_codes if c and not (c in seen or seen.add(c))]

            for ind in indicators:
                combo_count += 1
                payload_base: dict[str, Any] = {
                    "areaCode": str(ac),
                    "layerCode": list(layer_codes),
                    "indicator": ind.indicator,
                    **defaults,
                }
                payload = {"year": str(request_year), **payload_base}
                try:
                    if ns.playwright:
                        resp = post_indicator_getall_via_browser(
                            cookies_path=cookies or "cookies.json", payload=payload
                        )
                    else:
                        assert sess is not None
                        resp = request_json_with_retries(sess, "POST", API_URL, retry=retry, json=payload)
                except Exception as e:
                    ef.write(
                        json.dumps(
                            {
                                "payload": payload,
                                "error": str(e),
                                "at": datetime.now(timezone.utc).isoformat(),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    continue
                if not resp.get("success") or str(resp.get("code")) != "200":
                    ef.write(
                        json.dumps(
                            {
                                "payload": payload,
                                "error": f"bad_response code={resp.get('code')} msg={resp.get('message')}",
                                "at": datetime.now(timezone.utc).isoformat(),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    continue

                items = _parse_year_data_items(resp.get("data") or {})
                for it in items:
                    yr = int(it.get("year") or request_year)
                    layer_code = str(it.get("regionLayerCode") or "").strip() or "00"
                    province_cn = str(it.get("regionName") or "").strip()
                    province = layer_by_code.get(layer_code).name if layer_code in layer_by_code else province_cn

                    total_n = int(it.get("total") or 0)
                    resistant_percent = float(it.get("rate") or 0.0) * 100.0

                    # Prefer hitTotal if present; else compute.
                    if it.get("hitTotal") is not None:
                        resistant_n = int(it.get("hitTotal") or 0)
                    else:
                        resistant_n = _round_resistant_n(total_n, resistant_percent)

                    sensitive_percent = 100.0 - resistant_percent
                    sensitive_n = total_n - resistant_n

                    bacteria_species = ind.name.strip() if ind.name.strip() else _indicator_label_from_item(it, ind.indicator)

                    w.writerow(
                        {
                            "year": yr,
                            "province": province,
                            "layer_code": layer_code,
                            "bacteria_species": bacteria_species,
                            "organism_code": ind.indicator,
                            "drug_code": "",
                            "drug_full_name_cn": "",
                            "drug_full_name_en": "",
                            "total_n_strains": total_n,
                            "resistant_percent": resistant_percent,
                            "resistant_n_strains": resistant_n,
                            "intermediate_percent": 0.0,
                            "intermediate_n_strains": 0,
                            "sensitive_percent": sensitive_percent,
                            "sensitive_n_strains": sensitive_n,
                        }
                    )
                    written += 1

                if combo_count % 10 == 0:
                    print(f"[carss_indicator] combos={combo_count} rows={written}", file=sys.stderr)

                if ns.limit_combos and combo_count >= ns.limit_combos:
                    break
            if ns.limit_combos and combo_count >= ns.limit_combos:
                break

    print(f"[carss_indicator] done combos={combo_count} rows={written} -> {out_path}", file=sys.stderr)
    return 0

