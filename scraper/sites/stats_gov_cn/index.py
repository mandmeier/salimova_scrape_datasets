from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ...http import RetryConfig, request_bytes_with_retries


@dataclass(frozen=True)
class YearbookSection:
    yearbook_year: int
    chapter_id: str
    chapter_name: str
    section_id: str
    section_name: str
    image_link: str


_SECTION_ID_RE = re.compile(r"^E(?P<chapter>\d{2})-(?P<section>\d{2})\.jpg$", re.IGNORECASE)
# Older yearbooks use different naming conventions, e.g.:
# - E0101.jpg (no dash)
# - EN0101.jpg (EN prefix)
# - 0101EN.jpg (EN suffix)
_SECTION_ID_NODASH_RE = re.compile(r"^E(?P<chapter>\d{2})(?P<section>\d{2})\.jpg$", re.IGNORECASE)
_SECTION_ID_EN_PREFIX_RE = re.compile(r"^EN(?P<chapter>\d{2})(?P<section>\d{2})\.jpg$", re.IGNORECASE)
_SECTION_ID_EN_SUFFIX_RE = re.compile(r"^(?P<chapter>\d{2})(?P<section>\d{2})EN\.jpg$", re.IGNORECASE)

# Older yearbooks (at least 2005–2014) link to per-table HTML pages like:
# - html/A0101e.htm    -> html/A0101E.jpg
# - zk/html/Z0101e.htm -> zk/html/Z0101E.jpg
_SECTION_PAGE_RE = re.compile(
    r"^(?P<prefix>[A-Z])(?P<chapter>\d{2})(?P<section>\d{2})(?P<lang>[eE])\.htm$",
    re.IGNORECASE,
)


def _infer_image_from_section_page_href(href: str) -> tuple[str, str, str] | None:
    """
    For older yearbooks where the nav points to an .htm page per table, infer the .jpg.
    Returns (chapter_id, section_id, inferred_jpg_filename).
    """
    base = re.split(r"[\\\\/]+", (href or "").strip())[-1]
    m = _SECTION_PAGE_RE.match(base)
    if not m:
        return None
    chap = str(int(m.group("chapter")))
    sec = f"{chap}-{int(m.group('section'))}"
    root = os.path.splitext(base)[0]
    if root and root[-1].lower() == "e":
        root = root[:-1] + "E"
    jpg = f"{root}.jpg"
    return chap, sec, jpg


def _parse_section_ids_from_filename(filename: str) -> tuple[str, str] | None:
    """
    Return (chapter_id, section_id) inferred from a yearbook JPG filename.

    Normal form (2021+): E01-01.jpg -> ("1", "1-1")
    Older forms: E0101.jpg / EN0101.jpg / 0101EN.jpg -> ("1", "1-1")
    """
    base = re.split(r"[\\\\/]+", (filename or "").strip())[-1]
    for rx in (
        _SECTION_ID_RE,
        _SECTION_ID_NODASH_RE,
        _SECTION_ID_EN_PREFIX_RE,
        _SECTION_ID_EN_SUFFIX_RE,
    ):
        m = rx.match(base)
        if not m:
            continue
        chap = str(int(m.group("chapter")))
        sec = f"{chap}-{int(m.group('section'))}"
        return chap, sec
    return None


def _decode_yearbook_html(b: bytes) -> str:
    # Yearbook pages are usually gb2312/gbk compatible; gb18030 is a superset.
    return b.decode("gb18030", errors="replace")


def _find_left_nav_url(index_url: str, index_html: str) -> str:
    soup = BeautifulSoup(index_html, "lxml")
    for fr in soup.select("frame[src]"):
        src = (fr.get("src") or "").strip()
        if src.lower().startswith("left"):
            return urljoin(index_url, src)
    # fallback
    return urljoin(index_url, "left_.htm")


def parse_yearbook_index(
    *,
    year: int,
    index_url: str,
    sess,
    retry: RetryConfig,
) -> list[YearbookSection]:
    """
    Parse the left navigation of the yearbook and produce a flat list of sections pointing to JPGs.
    Chapter/section names come from link text.
    Chapter id is inferred from E##-##.jpg prefix. Chapter name is inferred from nearby nav headers.
    """
    idx_html = _decode_yearbook_html(request_bytes_with_retries(sess, "GET", index_url, retry=retry))
    left_url = _find_left_nav_url(index_url, idx_html)
    left_html = _decode_yearbook_html(request_bytes_with_retries(sess, "GET", left_url, retry=retry))

    soup = BeautifulSoup(left_html, "lxml")

    # Heuristic for chapter header text:
    # The nav is a long list where some anchors point to chapter splash images like ge01.jpg.
    # We'll treat anchors whose href matches 'html/ge\\d\\d\\.jpg' as chapter headers and use their text.
    chapter_id = ""
    chapter_name = ""

    sections: list[YearbookSection] = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        text = (" ".join((a.get_text() or "").split())).strip()
        if not href:
            continue

        # chapter header detection (e.g. html/ge01.jpg)
        if re.search(r"/?html/ge\d{2}\.jpg$", href, flags=re.IGNORECASE):
            # Infer chapter id from geXX.jpg
            m = re.search(r"ge(\d{2})\.jpg$", href, flags=re.IGNORECASE)
            if m:
                chapter_id = str(int(m.group(1)))  # drop leading zero
                chapter_name = text or chapter_name or f"Chapter {chapter_id}"
            continue

        # section image detection (supports multiple filename conventions across years)
        base = re.split(r"[\\\\/]+", href)[-1]
        parsed = _parse_section_ids_from_filename(base)
        if parsed:
            chap, sec = parsed
            image_link = urljoin(left_url, href)
        else:
            inferred = _infer_image_from_section_page_href(href)
            if not inferred:
                continue
            chap, sec, jpg = inferred
            parts = re.split(r"[\\\\/]+", href)
            dirpart = "/".join(parts[:-1]) + "/" if len(parts) > 1 else ""
            image_link = urljoin(left_url, dirpart + jpg)

        sections.append(
            YearbookSection(
                yearbook_year=year,
                chapter_id=chap,
                chapter_name=chapter_name or f"Chapter {chap}",
                section_id=sec,
                section_name=text or base,
                image_link=image_link,
            )
        )

    # De-dup by image_link (left nav may repeat)
    uniq: list[YearbookSection] = []
    seen = set()
    for s in sections:
        if s.image_link in seen:
            continue
        seen.add(s.image_link)
        uniq.append(s)

    return uniq

