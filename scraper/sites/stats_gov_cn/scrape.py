from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

from ...http import RetryConfig, make_session, request_bytes_with_retries
from .index import YearbookSection, parse_yearbook_index
from .download import download_images
from .download_pages import download_table_pages
from .province_detect import (
    detect_provinces_in_text,
    get_default_province_en_list_path,
    get_default_province_list_path,
    load_provinces_cn,
)
from .ocr_text import try_ocr_text
from .extract_table import extract_table_tesseract_grid, extract_yearbook_table_color_anchored


def index_url_for_year(year: int) -> str:
    return f"https://www.stats.gov.cn/sj/ndsj/{year}/indexeh.htm"


def _safe_name_from_url(url: str) -> str:
    p = urlparse(url)
    base = os.path.basename(p.path) or "file"
    # keep extension if present
    return base


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_yearbook_index_csv(out_dir: str, sections: list[YearbookSection]) -> str:
    path = os.path.join(out_dir, "yearbook_index.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "yearbook_year",
                "chapter_id",
                "chapter_name",
                "section_id",
                "section_name",
                "image_link",
            ],
        )
        w.writeheader()
        for s in sections:
            w.writerow(
                {
                    "yearbook_year": s.yearbook_year,
                    "chapter_id": s.chapter_id,
                    "chapter_name": s.chapter_name,
                    "section_id": s.section_id,
                    "section_name": s.section_name,
                    "image_link": s.image_link,
                }
            )
    return path


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
    ap.add_argument("--index", action="store_true", help="Write yearbook_index.csv")
    ap.add_argument("--download", action="store_true", help="Download discovered images.")
    ap.add_argument("--download-pages", action="store_true", help="Download per-table HTML pages for offline extraction.")
    ap.add_argument("--download-workers", type=int, default=1, help="Parallel download workers (default 1).")
    ap.add_argument("--detect-provinces", action="store_true", help="Detect province-level tables via OCR text pass.")
    ap.add_argument("--extract-provinces", action="store_true", help="Extract province-level tables to CSV.")
    ap.add_argument("--chapter", default="", help="Optional chapter id filter (e.g. 12).")
    ap.add_argument("--limit", type=int, default=0, help="Limit number of images (debug).")
    ns, _ = ap.parse_known_args(extra_args)

    if not year:
        raise SystemExit("stats_gov_cn requires --year (e.g. --year 2024)")

    out_dir = out or os.path.join("out", "stats_gov_cn", str(year))
    os.makedirs(out_dir, exist_ok=True)

    retry = RetryConfig(max_attempts=max_attempts, min_sleep_s=sleep, max_sleep_s=max_sleep)
    sess = make_session(timeout_s=timeout, headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
    # cookies not expected for this site; ignore if provided

    sections = parse_yearbook_index(year=year, index_url=index_url_for_year(year), sess=sess, retry=retry)
    if ns.chapter:
        sections = [s for s in sections if str(s.chapter_id) == str(ns.chapter)]
    if ns.limit:
        sections = sections[: ns.limit]

    manifest_path = os.path.join(out_dir, "manifest.jsonl")
    meta_path = os.path.join(out_dir, "discovery.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "year": year,
                "index_url": index_url_for_year(year),
                "sections": len(sections),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    index_csv_path = ""
    if ns.index or (not ns.download and not ns.download_pages):
        index_csv_path = write_yearbook_index_csv(out_dir, sections)

    if not ns.download and not ns.download_pages and not ns.detect_provinces and not ns.extract_provinces:
        print(f"[stats_gov_cn] indexed {len(sections)} sections.")
        if index_csv_path:
            print(f"[stats_gov_cn] wrote {index_csv_path}")
        print(f"[stats_gov_cn] wrote {meta_path}")
        return 0

    if ns.download_pages:
        downloaded, manifest_path = download_table_pages(
            out_dir=out_dir,
            year=year,
            index_url=index_url_for_year(year),
            sess=sess,
            retry=retry,
            workers=ns.download_workers,
        )
        print(f"[stats_gov_cn] downloaded {len(downloaded)} pages -> {os.path.join(out_dir, 'pages')}")
        print(f"[stats_gov_cn] pages manifest -> {manifest_path}")
        return 0

    # Province detection mode: ensure we have images locally, then OCR+filter.
    if ns.detect_provinces:
        images_dir = os.path.join(out_dir, "images")
        os.makedirs(images_dir, exist_ok=True)
        downloaded, manifest_path = download_images(
            out_dir=images_dir,
            sections=sections,
            sess=sess,
            retry=retry,
            workers=ns.download_workers,
        )

        provinces_cn = load_provinces_cn(get_default_province_list_path())
        provinces_en = load_provinces_cn(get_default_province_en_list_path())
        out_csv = os.path.join(out_dir, "yearbook_index_province.csv")
        with open(out_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "yearbook_year",
                    "chapter_id",
                    "chapter_name",
                    "section_id",
                    "section_name",
                    "image_link",
                    "province_match_count",
                    "province_matches",
                    "ocr_langs_used",
                ],
            )
            w.writeheader()
            kept = 0
            for i, d in enumerate(downloaded, start=1):
                # Prefer chi+eng if installed; fall back to eng-only.
                res = try_ocr_text(d.file_path, lang_candidates=["chi_sim+eng", "eng"])
                ocr_txt = res.text
                det_cn = detect_provinces_in_text(ocr_txt, provinces_cn)
                det_en = detect_provinces_in_text(ocr_txt, provinces_en)
                det = det_cn if det_cn.match_count >= det_en.match_count else det_en

                if det.match_count >= 5:
                    kept += 1
                    w.writerow(
                        {
                            "yearbook_year": d.section.yearbook_year,
                            "chapter_id": d.section.chapter_id,
                            "chapter_name": d.section.chapter_name,
                            "section_id": d.section.section_id,
                            "section_name": d.section.section_name,
                            "image_link": d.section.image_link,
                            "province_match_count": det.match_count,
                            "province_matches": "|".join(det.matched),
                            "ocr_langs_used": "chi_sim+eng|eng",
                        }
                    )
                if i % 25 == 0:
                    print(f"[stats_gov_cn] province-detect scanned {i}/{len(downloaded)} kept={kept}", file=sys.stderr)

        print(f"[stats_gov_cn] province tables: {kept}/{len(downloaded)} -> {out_csv}")
        return 0

    if ns.extract_provinces:
        # Expect yearbook_index_province.csv already exists; if not, run detection first.
        idx_prov = os.path.join(out_dir, "yearbook_index_province.csv")
        if not os.path.exists(idx_prov):
            raise RuntimeError(f"Missing {idx_prov}. Run with --detect-provinces first.")

        # Read province subset and reconstruct YearbookSection list
        prov_sections: list[YearbookSection] = []
        with open(idx_prov, "r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                if ns.chapter and str(row.get("chapter_id", "")) != str(ns.chapter):
                    continue
                prov_sections.append(
                    YearbookSection(
                        yearbook_year=int(row["yearbook_year"]),
                        chapter_id=row["chapter_id"],
                        chapter_name=row["chapter_name"],
                        section_id=row["section_id"],
                        section_name=row["section_name"],
                        image_link=row["image_link"],
                    )
                )

        if ns.limit:
            prov_sections = prov_sections[: ns.limit]

        images_dir = os.path.join(out_dir, "images")
        os.makedirs(images_dir, exist_ok=True)
        downloaded, _ = download_images(
            out_dir=images_dir,
            sections=prov_sections,
            sess=sess,
            retry=retry,
            workers=ns.download_workers,
        )

        tables_dir = os.path.join(out_dir, "tables")
        os.makedirs(tables_dir, exist_ok=True)

        long_path = os.path.join(out_dir, "yearbook_province_long.csv")
        with open(long_path, "w", encoding="utf-8", newline="") as lf:
            lw = csv.DictWriter(
                lf,
                fieldnames=[
                    "yearbook_year",
                    "chapter_id",
                    "chapter_name",
                    "section_id",
                    "section_name",
                    "province",
                    "column_name",
                    "value",
                    "source_image_link",
                ],
            )
            lw.writeheader()

            for d in downloaded:
                # Prefer the yearbook-specific anchored extractor; fall back to grid-based.
                anchored = extract_yearbook_table_color_anchored(d.file_path, langs="chi_sim+eng")
                if anchored is None:
                    anchored = extract_yearbook_table_color_anchored(d.file_path, langs="eng")

                if anchored is not None:
                    header = anchored.headers
                    rows = anchored.rows
                else:
                    tbl = extract_table_tesseract_grid(d.file_path, langs="eng")
                    grid = tbl.grid
                    if not grid:
                        continue
                    header = grid[0]
                    rows = grid[1:]

                # Write per-section wide CSV
                safe_id = d.section.section_id.replace("/", "-")
                out_csv = os.path.join(tables_dir, f"{safe_id}.csv")
                with open(out_csv, "w", encoding="utf-8", newline="") as wf:
                    ww = csv.writer(wf)
                    ww.writerow(["province", *header[1:]])
                    for rrow in rows:
                        if not rrow:
                            continue
                        province = rrow[0] if len(rrow) > 0 else ""
                        ww.writerow([province, *rrow[1:]])

                        # long-form emit
                        for j, val in enumerate(rrow[1:], start=1):
                            col = header[j] if j < len(header) else f"col_{j}"
                            lw.writerow(
                                {
                                    "yearbook_year": d.section.yearbook_year,
                                    "chapter_id": d.section.chapter_id,
                                    "chapter_name": d.section.chapter_name,
                                    "section_id": d.section.section_id,
                                    "section_name": d.section.section_name,
                                    "province": province,
                                    "column_name": col,
                                    "value": val,
                                    "source_image_link": d.section.image_link,
                                }
                            )

        print(f"[stats_gov_cn] wrote tables -> {tables_dir}")
        print(f"[stats_gov_cn] wrote long -> {long_path}")
        return 0

    downloaded, manifest_path = download_images(
        out_dir=out_dir,
        sections=sections,
        sess=sess,
        retry=retry,
        workers=ns.download_workers,
    )
    print(f"[stats_gov_cn] downloaded {len(downloaded)} images -> {out_dir}")
    print(f"[stats_gov_cn] manifest -> {manifest_path}")
    return 0

