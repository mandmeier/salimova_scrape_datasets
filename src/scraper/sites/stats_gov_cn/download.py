from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

from ...http import RetryConfig, request_bytes_with_retries
from .index import YearbookSection


@dataclass(frozen=True)
class DownloadedImage:
    section: YearbookSection
    file_path: str
    sha256: str
    size_bytes: int


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_existing_manifest(manifest_path: str, *, out_dir: str) -> tuple[dict[str, dict], int]:
    """
    Return (image_url -> manifest_record, record_count).
    If manifest doesn't exist or is unreadable, returns ({}, 0).
    """
    if not os.path.exists(manifest_path):
        return {}, 0

    by_url: dict[str, dict] = {}
    n = 0
    with open(manifest_path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = (ln or "").strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            url = str(rec.get("image_url") or "").strip()
            if not url:
                continue
            # Normalize stored file path to be relative to out_dir (as written).
            file_rel = str(rec.get("file") or "").strip()
            if file_rel:
                rec["file"] = file_rel
            by_url[url] = rec
            n += 1
    return by_url, n


def download_images(
    *,
    out_dir: str,
    sections: Iterable[YearbookSection],
    sess,
    retry: RetryConfig,
    manifest_filename: str = "manifest.jsonl",
    workers: int = 1,
) -> tuple[list[DownloadedImage], str]:
    os.makedirs(out_dir, exist_ok=True)
    manifest_path = os.path.join(out_dir, manifest_filename)

    existing_by_url, existing_count = _load_existing_manifest(manifest_path, out_dir=out_dir)

    # Pre-plan target paths deterministically to avoid collisions under parallelism.
    reserved: set[str] = set(os.listdir(out_dir))
    planned: list[tuple[YearbookSection, str, int]] = []
    next_i = existing_count + 1
    for s in sections:
        prev = existing_by_url.get(s.image_link)
        if prev:
            prev_file = str(prev.get("file") or "").strip()
            if prev_file and os.path.exists(os.path.join(out_dir, prev_file)):
                continue

        fname = os.path.basename(s.image_link)
        target_name = fname
        if target_name in reserved:
            prefix = hashlib.sha256(s.image_link.encode("utf-8")).hexdigest()[:10]
            root, ext = os.path.splitext(fname)
            target_name = f"{root}__{prefix}{ext}"
        reserved.add(target_name)
        planned.append((s, os.path.join(out_dir, target_name), next_i))
        next_i += 1

    downloaded: list[DownloadedImage] = []
    # Append-only manifest so downloads are resumable.
    manifest_mode = "a" if os.path.exists(manifest_path) else "w"

    with open(manifest_path, manifest_mode, encoding="utf-8") as mf:
        errors_path = os.path.join(out_dir, "manifest_errors.jsonl")
        errors_mode = "a" if os.path.exists(errors_path) else "w"
        ef = open(errors_path, errors_mode, encoding="utf-8")

        def _fetch_one(item: tuple[YearbookSection, str, int]) -> tuple[YearbookSection, str, int, bytes]:
            s, target, i = item
            content = request_bytes_with_retries(sess, "GET", s.image_link, retry=retry)
            return s, target, i, content

        # Parallel fetch; serial write-to-disk+manifest to keep manifest consistent.
        max_workers = max(1, int(workers or 1))
        if max_workers == 1:
            it = ( _fetch_one(item) for item in planned )
        else:
            pool = ThreadPoolExecutor(max_workers=max_workers)
            futures = [pool.submit(_fetch_one, item) for item in planned]
            it = as_completed(futures)

        for item in it:
            try:
                if max_workers == 1:
                    s, target, i, content = item  # type: ignore[misc]
                else:
                    s, target, i, content = item.result()  # type: ignore[union-attr]
            except Exception as e:
                # Do not fail the whole run if a single URL fails (404, timeout, etc).
                # Log the failure so we can triage/fix index inference later.
                msg = str(e)
                try:
                    # If this is a future, try to recover the section metadata from closure args.
                    sec = None
                    if max_workers != 1 and hasattr(item, "_args"):
                        sec = item._args[0][0]  # pragma: no cover
                except Exception:
                    sec = None
                ef.write(
                    json.dumps(
                        {
                            "error": msg,
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

            mf.write(
                json.dumps(
                    {
                        "i": i,
                        "yearbook_year": s.yearbook_year,
                        "chapter_id": s.chapter_id,
                        "chapter_name": s.chapter_name,
                        "section_id": s.section_id,
                        "section_name": s.section_name,
                        "image_url": s.image_link,
                        "file": os.path.relpath(target, out_dir),
                        "sha256": sha,
                        "bytes": len(content),
                        "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

            downloaded.append(
                DownloadedImage(section=s, file_path=target, sha256=sha, size_bytes=len(content))
            )

        ef.close()
        if max_workers != 1:
            pool.shutdown(wait=True)

    return downloaded, manifest_path

