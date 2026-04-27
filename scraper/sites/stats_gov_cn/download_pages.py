from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ...http import RetryConfig, request_bytes_with_retries
from .index import YearbookSection, _decode_yearbook_html, _find_left_nav_url, parse_yearbook_index  # type: ignore


@dataclass(frozen=True)
class DownloadedPage:
    section: YearbookSection
    file_path: str
    sha256: str
    size_bytes: int


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_existing_manifest(manifest_path: str) -> dict[str, dict]:
    if not os.path.exists(manifest_path):
        return {}
    by_url: dict[str, dict] = {}
    with open(manifest_path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = (ln or "").strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            url = str(rec.get("page_url") or "").strip()
            if not url:
                continue
            by_url[url] = rec
    return by_url


_TABLE_PAGE_HINT_RE = re.compile(r"([A-Z]\d{4}[eE]\.htm)$")


def _discover_table_pages(
    *,
    year: int,
    index_url: str,
    sess,
    retry: RetryConfig,
) -> list[YearbookSection]:
    """
    For older yearbooks (2005–2013), the left nav links to per-table .htm pages
    that contain the data table in HTML. We download these pages for offline extraction.
    """
    idx_html = _decode_yearbook_html(request_bytes_with_retries(sess, "GET", index_url, retry=retry))
    left_url = _find_left_nav_url(index_url, idx_html)
    left_html = _decode_yearbook_html(request_bytes_with_retries(sess, "GET", left_url, retry=retry))

    soup = BeautifulSoup(left_html, "lxml")
    chapter_id = ""
    chapter_name = ""
    out: list[YearbookSection] = []

    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        text = (" ".join((a.get_text() or "").split())).strip()
        if not href:
            continue

        href_norm = href.replace("\\", "/")

        # chapter header detection (e.g. html/ge01.jpg)
        if re.search(r"/?html/ge\d{2}\.jpg$", href_norm, flags=re.IGNORECASE):
            m = re.search(r"ge(\d{2})\.jpg$", href_norm, flags=re.IGNORECASE)
            if m:
                chapter_id = str(int(m.group(1)))
                chapter_name = text or chapter_name or f"Chapter {chapter_id}"
            continue

        if not href_norm.lower().endswith(".htm"):
            continue

        base = href_norm.split("/")[-1]
        if not _TABLE_PAGE_HINT_RE.search(base):
            # Keep it strict in minimal mode: only table-like pages with letter+4digits+e.htm
            continue

        # Reuse section id parsing logic from the filename (A0101e.htm -> 1-1, etc.)
        m2 = re.match(r"^(?P<prefix>[A-Z])(?P<chapter>\d{2})(?P<section>\d{2})[eE]\.htm$", base)
        if not m2:
            continue
        chap = str(int(m2.group("chapter")))
        sec = f"{chap}-{int(m2.group('section'))}"
        page_url = urljoin(left_url, href_norm)

        out.append(
            YearbookSection(
                yearbook_year=year,
                chapter_id=chap,
                chapter_name=chapter_name or f"Chapter {chap}",
                section_id=sec,
                section_name=text or base,
                image_link=page_url,  # field reused as page url
            )
        )

    # de-dup by url
    uniq: list[YearbookSection] = []
    seen = set()
    for s in out:
        if s.image_link in seen:
            continue
        seen.add(s.image_link)
        uniq.append(s)
    if uniq:
        return uniq

    # Fallback: some years serve a JS-only left nav with no anchors.
    # We can still enumerate sections via parse_yearbook_index (JPG urls), then infer the
    # per-table HTML page by replacing ".jpg" with "e.htm" / "E.htm".
    inferred: list[YearbookSection] = []
    for s in parse_yearbook_index(year=year, index_url=index_url, sess=sess, retry=retry):
        jpg_url = s.image_link
        base = os.path.basename(jpg_url)
        root, ext = os.path.splitext(base)
        if ext.lower() != ".jpg":
            continue
        # Many older years use per-table pages named like M0101e.htm even when the inferred
        # image basename ends with 'E' (e.g. M0101E.jpg). Drop the trailing 'E' before adding 'e.htm'.
        page_root = root[:-1] if root.endswith("E") else root
        page_name = f"{page_root}e.htm"
        page_url = jpg_url.rsplit("/", 1)[0] + "/" + page_name
        inferred.append(
            YearbookSection(
                yearbook_year=s.yearbook_year,
                chapter_id=s.chapter_id,
                chapter_name=s.chapter_name,
                section_id=s.section_id,
                section_name=s.section_name,
                image_link=page_url,
            )
        )

    # De-dup by url
    uniq2: list[YearbookSection] = []
    seen2 = set()
    for s in inferred:
        if s.image_link in seen2:
            continue
        seen2.add(s.image_link)
        uniq2.append(s)
    return uniq2


def download_table_pages(
    *,
    out_dir: str,
    year: int,
    index_url: str,
    sess,
    retry: RetryConfig,
    workers: int = 1,
    pages_subdir: str = "pages",
    manifest_filename: str = "pages_manifest.jsonl",
    errors_filename: str = "pages_errors.jsonl",
) -> tuple[list[DownloadedPage], str]:
    """
    Download per-table .htm pages into out_dir/pages/ with a resumable manifest.
    """
    os.makedirs(out_dir, exist_ok=True)
    pages_dir = os.path.join(out_dir, pages_subdir)
    os.makedirs(pages_dir, exist_ok=True)

    manifest_path = os.path.join(out_dir, manifest_filename)
    errors_path = os.path.join(out_dir, errors_filename)

    existing = _load_existing_manifest(manifest_path)
    sections = _discover_table_pages(year=year, index_url=index_url, sess=sess, retry=retry)
    if not sections:
        # Final fallback: use the already-generated local yearbook_index.csv (if present)
        # and infer page URLs from the stored image_link.
        idx_csv = os.path.join(out_dir, "yearbook_index.csv")
        if os.path.exists(idx_csv):
            import csv

            inferred: list[YearbookSection] = []
            with open(idx_csv, "r", encoding="utf-8", newline="") as f:
                r = csv.DictReader(f)
                for row in r:
                    img = (row.get("image_link") or "").strip()
                    if not img.lower().endswith(".jpg"):
                        continue
                    base = os.path.basename(img)
                    root, _ = os.path.splitext(base)
                    page_root = root[:-1] if root.endswith("E") else root
                    page_url = img.rsplit("/", 1)[0] + f"/{page_root}e.htm"
                    inferred.append(
                        YearbookSection(
                            yearbook_year=year,
                            chapter_id=(row.get("chapter_id") or "").strip(),
                            chapter_name=(row.get("chapter_name") or "").strip(),
                            section_id=(row.get("section_id") or "").strip(),
                            section_name=(row.get("section_name") or "").strip(),
                            image_link=page_url,
                        )
                    )
            # de-dup
            seen = set()
            sections = []
            for s in inferred:
                if s.image_link in seen:
                    continue
                seen.add(s.image_link)
                sections.append(s)

    # plan unique filenames
    reserved = set(os.listdir(pages_dir))
    planned: list[tuple[YearbookSection, str]] = []
    for s in sections:
        url = s.image_link
        prev = existing.get(url)
        if prev:
            prev_file = str(prev.get("file") or "").strip()
            if prev_file and os.path.exists(os.path.join(out_dir, prev_file)):
                continue

        fname = os.path.basename(url).replace("\\", "_").replace("/", "_")
        if fname in reserved:
            prefix = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
            root, ext = os.path.splitext(fname)
            fname = f"{root}__{prefix}{ext or '.htm'}"
        reserved.add(fname)
        planned.append((s, os.path.join(pages_dir, fname)))

    def fetch(item: tuple[YearbookSection, str]) -> tuple[YearbookSection, str, bytes]:
        s, target = item
        # Some years use ...E.htm rather than ...e.htm; try both.
        urls = [s.image_link]
        if s.image_link.lower().endswith("e.htm"):
            urls.append(s.image_link[:-5] + "E.htm")
        elif s.image_link.lower().endswith("e.htm".lower()) is False and s.image_link.lower().endswith(".htm"):
            # If caller passed ...E.htm already, also try ...e.htm
            if s.image_link.endswith("E.htm"):
                urls.append(s.image_link[:-5] + "e.htm")

        last_err: Exception | None = None
        for u in urls:
            try:
                content = request_bytes_with_retries(sess, "GET", u, retry=retry)
                if u != s.image_link:
                    # Update section url to the working one for manifest correctness.
                    s = YearbookSection(
                        yearbook_year=s.yearbook_year,
                        chapter_id=s.chapter_id,
                        chapter_name=s.chapter_name,
                        section_id=s.section_id,
                        section_name=s.section_name,
                        image_link=u,
                    )
                return s, target, content
            except Exception as e:
                last_err = e
                continue
        raise last_err or RuntimeError("Failed to fetch page")

    downloaded: list[DownloadedPage] = []
    man_mode = "a" if os.path.exists(manifest_path) else "w"
    err_mode = "a" if os.path.exists(errors_path) else "w"
    max_workers = max(1, int(workers or 1))

    with open(manifest_path, man_mode, encoding="utf-8") as mf, open(errors_path, err_mode, encoding="utf-8") as ef:
        if max_workers == 1:
            it = (fetch(x) for x in planned)
        else:
            pool = ThreadPoolExecutor(max_workers=max_workers)
            futures = [pool.submit(fetch, x) for x in planned]
            it = as_completed(futures)

        for item in it:
            try:
                if max_workers == 1:
                    s, target, content = item  # type: ignore[misc]
                else:
                    s, target, content = item.result()  # type: ignore[union-attr]
            except Exception as e:
                ef.write(
                    json.dumps(
                        {
                            "year": year,
                            "error": str(e),
                            "downloaded_at": datetime.now(timezone.utc).isoformat(),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                continue

            sha = _sha256(content)
            with open(target, "wb") as f:
                f.write(content)

            rec = {
                "year": year,
                "chapter_id": s.chapter_id,
                "chapter_name": s.chapter_name,
                "section_id": s.section_id,
                "section_name": s.section_name,
                "page_url": s.image_link,
                "file": os.path.relpath(target, out_dir),
                "sha256": sha,
                "bytes": len(content),
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
            }
            mf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            downloaded.append(DownloadedPage(section=s, file_path=target, sha256=sha, size_bytes=len(content)))

        if max_workers != 1:
            pool.shutdown(wait=True)

    return downloaded, manifest_path

