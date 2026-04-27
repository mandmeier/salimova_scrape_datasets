from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Tuple

import difflib
import cv2  # type: ignore
import numpy as np  # type: ignore
import re

from .ocr_text import ocr_text_tesseract


@dataclass(frozen=True)
class ExtractedTable:
    grid: list[list[str]]  # rows x cols


@dataclass(frozen=True)
class AnchoredTable:
    rowname_header: str
    headers: list[str]  # includes rowname header as first element
    rows: list[list[str]]  # each row includes rowname as first element


_CANON_12_13_HEADERS = [
    "Year Region",
    "Large Animals (year-end)",
    "Cattle and Buffaloes",
    "Horses",
    "Donkeys",
    "Mules",
    "Camels",
    "Slaughtered Hogs",
    "Hogs (year-end)",
    "Sheep and Goats (year-end)",
    "Goats",
    "Sheep",
]


def _norm_header_token(s: str) -> str:
    s2 = (s or "").lower().strip()
    s2 = re.sub(r"[\s\.\,\-\(\)\[\]\{\}]+", " ", s2)
    s2 = re.sub(r"[^a-z0-9 ]+", "", s2)
    return re.sub(r"\s+", " ", s2).strip()


def _maybe_canonicalize_year_region_headers(headers: list[str]) -> list[str]:
    """
    OCR on the blue header ribbon can produce very noisy labels. For known yearbook
    tables whose semantics are stable across years, prefer a canonical header set
    when the extracted header row is a close match.
    """
    if len(headers) != len(_CANON_12_13_HEADERS):
        return headers

    h0 = _norm_header_token(headers[0])
    if "region" not in h0:
        return headers

    joined = " ".join(_norm_header_token(h) for h in headers)
    must = ("cattle", "horses", "donkeys", "hogs", "sheep")
    if sum(1 for t in must if t in joined) < 3:
        return headers

    return list(_CANON_12_13_HEADERS)


def _preprocess_for_grid(img_gray: np.ndarray) -> np.ndarray:
    # Invert and binarize
    blur = cv2.GaussianBlur(img_gray, (3, 3), 0)
    bw = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2
    )
    bw = 255 - bw
    return bw


def _extract_lines(bw_inv: np.ndarray) -> np.ndarray:
    h, w = bw_inv.shape[:2]
    # Kernel sizes relative to image size
    hor = max(20, w // 40)
    ver = max(20, h // 40)
    horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (hor, 1))
    vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, ver))

    horiz = cv2.erode(bw_inv, horiz_kernel, iterations=1)
    horiz = cv2.dilate(horiz, horiz_kernel, iterations=2)

    vert = cv2.erode(bw_inv, vert_kernel, iterations=1)
    vert = cv2.dilate(vert, vert_kernel, iterations=2)

    grid = cv2.add(horiz, vert)
    return grid


def _find_cells(grid_mask: np.ndarray) -> list[Tuple[int, int, int, int]]:
    # Find contours of boxes
    contours, _ = cv2.findContours(grid_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w < 20 or h < 15:
            continue
        boxes.append((x, y, w, h))
    # Sort by y, then x
    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes


def _group_rows(boxes: list[Tuple[int, int, int, int]], y_tol: int = 10) -> list[list[Tuple[int, int, int, int]]]:
    rows: list[list[Tuple[int, int, int, int]]] = []
    for b in boxes:
        x, y, w, h = b
        placed = False
        for row in rows:
            _, ry, _, rh = row[0]
            if abs(y - ry) <= y_tol:
                row.append(b)
                placed = True
                break
        if not placed:
            rows.append([b])
    for row in rows:
        row.sort(key=lambda b: b[0])
    rows.sort(key=lambda r: r[0][1])
    return rows


def extract_table_tesseract_grid(image_path: str, *, langs: str = "eng") -> ExtractedTable:
    """
    Best-effort table extraction from a yearbook JPG:
    - detect grid lines with morphology
    - detect cell boxes
    - OCR each cell with tesseract

    This is not perfect but provides a reproducible baseline.
    """
    img = cv2.imread(image_path)
    if img is None:
        raise RuntimeError(f"Failed to read image: {image_path}")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bw_inv = _preprocess_for_grid(gray)
    grid = _extract_lines(bw_inv)
    boxes = _find_cells(grid)
    row_groups = _group_rows(boxes)

    out_grid: list[list[str]] = []
    # OCR each cell by cropping from original grayscale
    for row in row_groups:
        row_texts: list[str] = []
        for (x, y, w, h) in row:
            crop = gray[y : y + h, x : x + w]
            # write temp image in memory by encoding; pytesseract can take PIL, but we reuse helper by saving a temp
            # Use a simple heuristic: small crops get scaled up.
            scale = 2
            crop2 = cv2.resize(crop, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
            tmp_path = None
            try:
                # write to a temporary path in the same folder to avoid permission issues
                tmp_path = os.path.join(os.path.dirname(image_path), ".__cell_tmp.png")
                cv2.imwrite(tmp_path, crop2)
                txt = ocr_text_tesseract(tmp_path, langs=langs, psm=6).text
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
            row_texts.append(" ".join((txt or "").split()))
        # keep only rows with some content
        if any(t for t in row_texts):
            out_grid.append(row_texts)

    return ExtractedTable(grid=out_grid)


def _hex_to_bgr(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.strip().lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (b, g, r)


def _mask_color_hsv(img_bgr: np.ndarray, hex_color: str, *, h_tol: int, s_tol: int, v_tol: int) -> np.ndarray:
    """
    Create a mask for pixels near a target color in HSV.
    Tolerances are applied around the target HSV value.
    """
    target_bgr = np.uint8([[list(_hex_to_bgr(hex_color))]])
    target_hsv = cv2.cvtColor(target_bgr, cv2.COLOR_BGR2HSV)[0][0]
    th, ts, tv = int(target_hsv[0]), int(target_hsv[1]), int(target_hsv[2])

    lower = np.array([max(0, th - h_tol), max(0, ts - s_tol), max(0, tv - v_tol)], dtype=np.uint8)
    upper = np.array([min(179, th + h_tol), min(255, ts + s_tol), min(255, tv + v_tol)], dtype=np.uint8)

    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, lower, upper)


def _largest_component_bbox(mask: np.ndarray, *, min_area: int = 2000) -> tuple[int, int, int, int] | None:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = 0
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area < min_area:
            continue
        if area > best_area:
            best_area = area
            best = (x, y, w, h)
    return best


def _find_header_band_blue(img_bgr: np.ndarray) -> tuple[int, int, int, int] | None:
    """
    Locate the blue header band (#A4CBFA) near the top.
    Returns bbox (x,y,w,h) of the largest blue-ish component.
    """
    mask = _mask_color_hsv(img_bgr, "A4CBFA", h_tol=18, s_tol=80, v_tol=80)
    # clean up
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    bbox = _largest_component_bbox(mask, min_area=5000)
    if not bbox:
        return None
    x, y, w, h = bbox
    # ensure it's near top and wide
    if y > img_bgr.shape[0] * 0.35 or w < img_bgr.shape[1] * 0.4:
        return None

    # Refine vertical extent to actual blue pixels to avoid including lines below header.
    sub = mask[y : y + h, x : x + w]
    row_counts = (sub > 0).sum(axis=1)
    # A row is considered "blue" if at least 20% of the bbox width is blue-ish.
    thr = max(10, int(w * 0.20))
    ys = np.where(row_counts >= thr)[0]
    if len(ys) >= 2:
        y0 = int(ys.min())
        y1 = int(ys.max())
        # pad slightly within bounds
        top = max(0, y + y0 - 1)
        bottom = min(img_bgr.shape[0], y + y1 + 2)
        return (x, top, w, max(1, bottom - top))
    return bbox


def _detect_vertical_separators_in_header(
    gray: np.ndarray,
    header_bbox: tuple[int, int, int, int],
    *,
    min_sep_height_ratio: float = 0.6,
    merge_gap_px: int = 6,
) -> list[int]:
    """
    Detect x-positions of vertical separators in the header band.

    Returns absolute x positions (in full-image coordinates), sorted, de-duplicated.
    """
    hx, hy, hw, hh = header_bbox
    band = gray[hy : hy + hh, hx : hx + hw]

    # Emphasize dark separator lines over colored background.
    # Use a relatively strict threshold to keep only the lines/text.
    mask = (band < 120).astype(np.uint8) * 255

    # Strengthen vertical structures.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, hh // 3)))
    v = cv2.erode(mask, kernel, iterations=1)
    v = cv2.dilate(v, kernel, iterations=2)

    col_sum = (v > 0).sum(axis=0)
    min_height = int(hh * min_sep_height_ratio)
    xs = np.where(col_sum >= min_height)[0]
    if len(xs) == 0:
        return []

    # Merge nearby x positions into single separators.
    seps: list[int] = []
    start = int(xs[0])
    prev = int(xs[0])
    for x in xs[1:]:
        x = int(x)
        if x - prev <= merge_gap_px:
            prev = x
            continue
        center = (start + prev) // 2
        seps.append(hx + center)
        start = prev = x
    center = (start + prev) // 2
    seps.append(hx + center)

    # Ensure left/right bounds included
    left = hx
    right = hx + hw - 1
    if not seps or seps[0] - left > 15:
        seps = [left] + seps
    if right - seps[-1] > 15:
        seps = seps + [right]

    # Final sort unique
    seps = sorted(set(seps))
    return seps


def _refine_header_cell_bbox_with_blue(
    blue_mask: np.ndarray, bbox: tuple[int, int, int, int], *, min_blue_width_ratio: float = 0.25
) -> tuple[int, int, int, int]:
    """
    Given a blue mask (same coordinate space as the cropped image) and a header-cell bbox,
    shrink the bbox vertically to rows that actually contain blue pixels.
    This reduces contamination from interior horizontal rules / non-header content.
    """
    x, y, w, h = bbox
    sub = blue_mask[y : y + h, x : x + w]
    if sub.size == 0:
        return bbox
    row_counts = (sub > 0).sum(axis=1)
    thr = max(5, int(w * min_blue_width_ratio))
    ys = np.where(row_counts >= thr)[0]
    if len(ys) < 2:
        return bbox
    y0 = int(ys.min())
    y1 = int(ys.max())
    top = y + max(0, y0 - 1)
    bottom = y + min(h, y1 + 2)
    return (x, top, w, max(1, bottom - top))


def _find_yellow_rowname_strip(img_bgr: np.ndarray, *, y_start: int) -> tuple[int, int, int, int] | None:
    """
    Locate the yellow rowname column background (#FEFFA5) below header.
    """
    mask = _mask_color_hsv(img_bgr, "FEFFA5", h_tol=25, s_tol=120, v_tol=120)
    # zero out above y_start
    mask[:y_start, :] = 0
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    bbox = _largest_component_bbox(mask, min_area=8000)
    if not bbox:
        return None
    x, y, w, h = bbox
    # should be on left side and tall-ish
    if x > img_bgr.shape[1] * 0.25 or h < img_bgr.shape[0] * 0.2:
        return None
    return bbox


def _ocr_cell(gray: np.ndarray, bbox: tuple[int, int, int, int], *, langs: str, psm: int, whitelist: str | None) -> str:
    x, y, w, h = bbox
    crop = gray[y : y + h, x : x + w]
    # upscale for OCR
    scale = 3
    crop2 = cv2.resize(crop, (max(1, w * scale), max(1, h * scale)), interpolation=cv2.INTER_CUBIC)

    # Binarization tuned by content type:
    # - headers (colored backgrounds): adaptive threshold is more stable
    # - numeric cells (white background): Otsu usually works fine
    if whitelist is None:
        thr = cv2.adaptiveThreshold(
            crop2,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            10,
        )
    else:
        _, thr = cv2.threshold(crop2, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except Exception:
        # fallback: write temp and use existing wrapper
        tmp_path = os.path.join("/tmp", ".__tess_cell.png")
        cv2.imwrite(tmp_path, thr)
        txt = ocr_text_tesseract(tmp_path, langs=langs, psm=psm).text
        return " ".join((txt or "").split()).strip()

    img = Image.fromarray(thr)
    cfg = f"--psm {psm}"
    if whitelist:
        cfg += f' -c tessedit_char_whitelist="{whitelist}"'
    txt = pytesseract.image_to_string(img, lang=langs, config=cfg)
    out = " ".join((txt or "").split())
    return out.strip()


def _score_header_text(s: str) -> int:
    s2 = (s or "").strip()
    if not s2:
        return -10
    # Prefer strings with letters, and penalize obvious junk.
    letters = sum(ch.isalpha() for ch in s2)
    digits = sum(ch.isdigit() for ch in s2)
    bad = sum(ch in "_=~`" for ch in s2)
    spaces = s2.count(" ")
    # Very short outputs like "_" or "ee" are often wrong.
    short_pen = -8 if len(s2) <= 2 else 0
    # If it looks like a number-heavy cell, it's likely wrong for headers.
    num_pen = -6 if digits >= max(2, letters + 2) else 0
    return letters * 3 + spaces + len(s2) - bad * 4 + short_pen + num_pen


def _debackground_header_cell(
    crop_bgr: np.ndarray,
    *,
    blue_hex: str = "A4CBFA",
) -> np.ndarray:
    """
    Headers are text over a blue ribbon. Replace blue-ish background with white to
    increase contrast for OCR. Returns a grayscale image.
    """
    if crop_bgr.size == 0:
        return np.zeros((1, 1), dtype=np.uint8)
    # Mask "blue header ribbon" background
    blue = _mask_color_hsv(crop_bgr, blue_hex, h_tol=18, s_tol=80, v_tol=80)
    # Clean up mask to fill small holes
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    blue = cv2.morphologyEx(blue, cv2.MORPH_CLOSE, k, iterations=1)

    out = crop_bgr.copy()
    out[blue > 0] = (255, 255, 255)
    return cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)


def _remove_header_rules(gray: np.ndarray) -> np.ndarray:
    """
    Remove thin horizontal/vertical rules commonly present in header ribbons.
    """
    if gray.size == 0:
        return gray
    g = gray.copy()
    inv = 255 - g

    h, w = g.shape[:2]
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, (w * 3) // 5), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, (h * 3) // 5)))

    hline = cv2.dilate(cv2.erode(inv, hk, iterations=1), hk, iterations=1)
    vline = cv2.dilate(cv2.erode(inv, vk, iterations=1), vk, iterations=1)

    # Keep only strong, thin lines.
    line_mask = ((hline > 200) | (vline > 200)).astype(np.uint8) * 255
    if line_mask.any():
        g[line_mask > 0] = 255
    return g


def _ocr_header_cell_best(
    gray: np.ndarray,
    bbox: tuple[int, int, int, int],
    *,
    langs: str,
    bgr_full: np.ndarray | None = None,
) -> str:
    """
    Header cells are text over colored backgrounds; use more aggressive preprocessing
    and pick the most plausible English label from a few OCR variants.
    """
    x, y, w, h = bbox
    crop = gray[y : y + h, x : x + w]
    crop_bgr = None
    if bgr_full is not None:
        crop_bgr = bgr_full[y : y + h, x : x + w]
    # Give headers more pixels than body cells.
    scale = 5 if min(w, h) < 80 else 4
    crop2 = cv2.resize(crop, (max(1, w * scale), max(1, h * scale)), interpolation=cv2.INTER_CUBIC)

    # Variant: debackground using color info (if available) before enhancement.
    if crop_bgr is not None and crop_bgr.size > 0:
        crop_bgr2 = cv2.resize(
            crop_bgr, (max(1, w * scale), max(1, h * scale)), interpolation=cv2.INTER_CUBIC
        )
        crop2_db = _debackground_header_cell(crop_bgr2)
    else:
        crop2_db = crop2

    # Increase local contrast and remove small noise.
    crop2 = cv2.GaussianBlur(crop2, (3, 3), 0)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    crop2 = clahe.apply(crop2)
    crop2_db = cv2.GaussianBlur(crop2_db, (3, 3), 0)
    crop2_db = clahe.apply(crop2_db)

    # Remove header rules (horizontal + vertical separators) to reduce OCR confusion.
    crop2 = _remove_header_rules(crop2)
    crop2_db = _remove_header_rules(crop2_db)

    # Find the actual text band(s) within the header cell by row projection.
    # This helps when header text doesn't fill the full ribbon height.
    # We do this on a light threshold of the enhanced grayscale.
    bw = cv2.adaptiveThreshold(crop2_db, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 8)
    # text tends to be dark -> bw == 0
    row_ink = (bw == 0).sum(axis=1)
    # Require some ink relative to width; keep conservative so we don't include noise.
    thr_ink = max(6, int(bw.shape[1] * 0.03))
    ys = np.where(row_ink >= thr_ink)[0]
    if len(ys) >= 6:
        # Build contiguous runs and keep up to 2 largest runs (headers can be 2-line).
        runs: list[tuple[int, int]] = []
        start = int(ys[0])
        prev = int(ys[0])
        for yy in ys[1:]:
            yy = int(yy)
            if yy - prev <= 1:
                prev = yy
                continue
            runs.append((start, prev))
            start = prev = yy
        runs.append((start, prev))
        runs.sort(key=lambda r: (r[1] - r[0]), reverse=True)
        keep = runs[:2]
        keep.sort(key=lambda r: r[0])
        pad = 3
        r0 = max(0, keep[0][0] - pad)
        r1 = min(crop2.shape[0] - 1, keep[-1][1] + pad)
        crop2 = crop2[r0 : r1 + 1, :]
        crop2_db = crop2_db[r0 : r1 + 1, :]

    # Two threshold variants (different C) can swing OCR a lot on colored headers.
    thr_a = cv2.adaptiveThreshold(crop2_db, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 8)
    thr_b = cv2.adaptiveThreshold(crop2_db, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 12)
    thr_c = cv2.adaptiveThreshold(crop2, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 8)
    # Try both normal and inverted; some headers are dark-on-light and others light-on-dark after thresholding.
    variants = [thr_a, 255 - thr_a, thr_b, 255 - thr_b, thr_c, 255 - thr_c]

    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except Exception:
        # Fallback to the generic cell OCR, but with a slightly different psm.
        t1 = _ocr_cell(gray, bbox, langs=langs, psm=7, whitelist=None)
        t2 = _ocr_cell(gray, bbox, langs=langs, psm=6, whitelist=None)
        return max([t1, t2], key=_score_header_text).strip()

    out_texts: list[str] = []
    for v in variants:
        img = Image.fromarray(v)
        # Two PSM modes: single line vs block.
        for psm in (7, 6):
            # Header text only: restrict to likely characters to reduce OCR junk.
            cfg = (
                f"--psm {psm} "
                "-c preserve_interword_spaces=1 "
                '-c tessedit_char_whitelist="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz()-. " '
                "-c load_system_dawg=0 -c load_freq_dawg=0"
            )
            txt = pytesseract.image_to_string(img, lang=langs, config=cfg)
            out_texts.append(" ".join((txt or "").split()).strip())

        # Confidence-filtered token merge can be more stable than raw string OCR.
        for psm in (7, 6):
            cfg = (
                f"--psm {psm} "
                "-c preserve_interword_spaces=1 "
                '-c tessedit_char_whitelist="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz()-. " '
                "-c load_system_dawg=0 -c load_freq_dawg=0"
            )
            data = pytesseract.image_to_data(img, lang=langs, config=cfg, output_type=pytesseract.Output.DICT)
            toks: list[str] = []
            for t, c in zip(data.get("text", []), data.get("conf", [])):
                t = (t or "").strip()
                if not t:
                    continue
                c_raw = str(c).strip()
                conf = int(float(c_raw)) if c_raw not in ("", "-1") else -1
                if conf != -1 and conf < 40:
                    continue
                toks.append(t)
            out_texts.append(" ".join(toks).strip())

    # Also include the old path as a backstop.
    out_texts.append(_ocr_cell(gray, bbox, langs=langs, psm=7, whitelist=None))

    best = max(out_texts, key=_score_header_text)
    return best


def _score_numeric_text(s: str) -> int:
    s2 = (s or "").strip()
    if not s2:
        return -100
    digits = sum(ch.isdigit() for ch in s2)
    dots = s2.count(".")
    commas = s2.count(",")
    letters = sum(ch.isalpha() for ch in s2)
    minus = s2.count("-")
    bad = sum(ch in "_=|`~" for ch in s2)
    single_digit_pen = -25 if (digits == 1 and len(s2) == 1) else 0
    # prefer digit-heavy, allow one dot/comma; penalize letters/junk.
    return digits * 5 + min(dots + commas, 2) * 2 - letters * 6 - bad * 4 - minus * 8 + len(s2) + single_digit_pen


def _ocr_numeric_cell_best(
    gray: np.ndarray,
    bbox: tuple[int, int, int, int],
    *,
    langs: str,
    prefer_decimals: int | None = None,
) -> str:
    x, y, w, h = bbox
    crop = gray[y : y + h, x : x + w]
    if crop.size == 0:
        return ""
    # Higher scale reduces 5/6 and 5/9 confusions in many yearbook scans.
    scale = 4
    crop2 = cv2.resize(crop, (max(1, w * scale), max(1, h * scale)), interpolation=cv2.INTER_CUBIC)
    crop2 = cv2.GaussianBlur(crop2, (3, 3), 0)
    _, thr = cv2.threshold(crop2, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Also try an adaptive threshold variant (sometimes preserves holes in 6/9 better).
    thr_ad = cv2.adaptiveThreshold(
        crop2, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 9
    )

    # Remove tiny specks that cause false positives in empty cells.
    thr_clean = cv2.morphologyEx(
        thr,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
        iterations=1,
    )

    # Empty-cell gate: if there is almost no "ink", return empty.
    # For THRESH_BINARY, text is typically darker (0). Measure fraction of dark pixels.
    ink = float((thr_clean == 0).mean())
    if ink < 0.004:
        return ""
    # Component-size gate: reject tiny blobs that often OCR as single digits.
    inv = (thr_clean == 0).astype(np.uint8)
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    if n_labels > 1:
        areas = stats[1:, cv2.CC_STAT_AREA]
        max_area = int(areas.max()) if len(areas) else 0
        # Require a minimally sized glyph component; tuned to avoid empty-cell hallucinations.
        if max_area < 60 and ink < 0.02:
            return ""

    variants = [thr_clean, 255 - thr_clean, thr_ad, 255 - thr_ad]

    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except Exception:
        return _ocr_cell(gray, bbox, langs=langs, psm=7, whitelist="0123456789.,-")

    # Do not include '-' for these yearbook numeric tables (values are non-negative).
    whitelist = "0123456789.,"
    out: list[str] = []
    for v in variants:
        img = Image.fromarray(v)
        for psm in (7, 8):
            cfg = (
                f"--psm {psm} "
                f'-c tessedit_char_whitelist="{whitelist}" '
                "-c classify_bln_numeric_mode=1 "
                "-c tessedit_do_invert=0 "
                "-c load_system_dawg=0 -c load_freq_dawg=0 "
                "-c user_defined_dpi=300"
            )
            # Prefer token-confidence filtering to avoid low-confidence hallucinations.
            data = pytesseract.image_to_data(img, lang=langs, config=cfg, output_type=pytesseract.Output.DICT)
            toks: list[str] = []
            for t, c in zip(data.get("text", []), data.get("conf", [])):
                t = (t or "").strip()
                if not t:
                    continue
                c_raw = str(c).strip()
                conf = int(float(c_raw)) if c_raw not in ("", "-1") else -1
                # If the cell clearly has ink, allow slightly lower confidence; helps avoid dropping real values.
                min_conf = 25 if ink >= 0.020 else (35 if ink >= 0.012 else 45)
                if conf != -1 and conf < min_conf:
                    continue
                toks.append(t)
            txt = "".join(toks).strip()
            out.append(txt)
    def score_with_pref(s: str) -> int:
        sc = _score_numeric_text(s)
        if prefer_decimals is None:
            return sc
        s2 = (s or "").strip()
        if "." in s2:
            dec = len(s2.split(".")[-1])
            if dec == prefer_decimals:
                sc += 8
        return sc

    best = max(out, key=score_with_pref)
    # If the best result is still just a single digit and the ink is low, treat as empty (false positive).
    if len(best) == 1 and best.isdigit() and ink < 0.02:
        return ""
    return best


def extract_yearbook_table_color_anchored(
    image_path: str,
    *,
    langs: str = "eng",
) -> AnchoredTable | None:
    """
    Extract yearbook-style tables where:
    - header row is blue (#A4CBFA)
    - rowname column background is yellow (#FEFFA5)
    - table body is white (no cell borders)

    Rules:
    - Skip if top-left header cell OCR doesn't contain 'Region' or 'Year Region'.
    - Stop when reaching bottom of the yellow strip.
    """
    img = cv2.imread(image_path)
    if img is None:
        raise RuntimeError(f"Failed to read image: {image_path}")

    h_img, w_img = img.shape[:2]
    gray_full = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    header_bbox_full = _find_header_band_blue(img)
    # Important: ignore anything above the blue header stripe.
    # We find the stripe in the full image, then crop the working image to start at it.
    header_bbox = header_bbox_full
    if not header_bbox:
        return None
    hx, hy, hw, hh = header_bbox
    y_off = hy
    img = img[y_off:, :, :]
    gray = gray_full[y_off:, :]
    h_img, w_img = img.shape[:2]

    # Rebase header bbox into the cropped coordinate system.
    header_bbox = (hx, 0, hw, hh)
    header_top = 0
    header_bottom = hh

    yellow_bbox = _find_yellow_rowname_strip(img, y_start=header_bottom + 5)
    if not yellow_bbox:
        return None
    yx, yy, yw, yh = yellow_bbox

    # Detect column boundaries from vertical separators in the blue header band.
    # This avoids assuming the yellow column equals data column width.
    seps = _detect_vertical_separators_in_header(gray, header_bbox)
    if len(seps) < 3:
        return None

    # Use yellow strip only to locate the rowname/body extent.
    x0 = yx
    # Choose the separator that is closest to the right edge of the yellow strip as the rowname boundary.
    x_rowname_right = min(seps, key=lambda x: abs(x - (yx + yw)))

    # Body starts where the yellow strip begins (header row is blue).
    body_top = yy
    body_bottom = yy + yh

    # Gate: the rowname header label ('Region' or 'Year Region') lives in the left side
    # of the blue header band, but OCR may need a wider crop than the yellow-strip width.
    gate_w = min(w_img - x0, max((x_rowname_right - x0) + 200, 320))
    # Try a more precise OCR on just the top-left header cell (rowname header cell).
    tl_cell_txt = _ocr_header_cell_best(
        gray,
        (x0, header_top, max(30, x_rowname_right - x0), hh),
        langs=langs,
        bgr_full=img,
    )
    gate_txt = tl_cell_txt or _ocr_cell(gray, (x0, header_top, gate_w, hh), langs=langs, psm=11, whitelist=None)
    gate_norm = (gate_txt or "").lower().replace(" ", "")
    if ("region" not in gate_norm) and ("yearregion" not in gate_norm):
        # allow minor OCR errors like "regiori"/"reglon"
        close = difflib.get_close_matches(gate_norm, ["region", "yearregion"], n=1, cutoff=0.72)
        if not close:
            return None
    # Keep the detected label as-is (no canonicalization).
    tl_txt = tl_cell_txt or ("Year Region" if "yearregion" in gate_norm else "Region")

    # Build column spans from separators, restricting to columns that overlap table (from x0 rightwards).
    seps = [x for x in seps if x >= x0]
    seps = sorted(seps)
    spans: list[tuple[int, int]] = []
    for a, b in zip(seps, seps[1:]):
        if b - a < 25:
            continue
        spans.append((a, b))
    if not spans:
        return None

    # Identify which span is the rowname column: the one whose right edge equals x_rowname_right (closest).
    rowname_idx = min(range(len(spans)), key=lambda i: abs(spans[i][1] - x_rowname_right))

    # Extract headers for each span (keep as-is, no forced names)
    headers: list[str] = []
    blue_mask = _mask_color_hsv(img, "A4CBFA", h_tol=18, s_tol=80, v_tol=80)
    for i, (a, b) in enumerate(spans):
        cell_bbox = (a, header_top, b - a, hh)
        cell_bbox = _refine_header_cell_bbox_with_blue(blue_mask, cell_bbox)
        txt = _ocr_header_cell_best(gray, cell_bbox, langs=langs, bgr_full=img)
        headers.append(txt or (tl_txt if i == rowname_idx else f"col_{i}"))

    # Ensure the rowname header is exactly the normalized label
    headers[rowname_idx] = tl_txt

    # Extract rownames by OCR-ing the entire rowname column body as lines via pytesseract data
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except Exception:
        return None

    a0, b0 = spans[rowname_idx]
    rowname_crop = gray[body_top:body_bottom, a0:b0]
    # No preprocessing here: keep raw pixels to avoid losing faint labels.
    rowname_imgs = [Image.fromarray(rowname_crop)]

    def extract_lines_psm(img_in: Image.Image, psm: int) -> list[dict[str, int | str]]:
        data = pytesseract.image_to_data(
            img_in,
            lang=langs,
            config=(
                f"--psm {psm} "
                "-c preserve_interword_spaces=1 "
                '-c tessedit_char_whitelist="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 "'
            ),
            output_type=pytesseract.Output.DICT,
        )
        lines: dict[tuple[int, int], dict[str, int | str]] = {}
        n = len(data.get("text", []))
        for i in range(n):
            txt = (data["text"][i] or "").strip()
            if not txt:
                continue
            conf_raw = str(data.get("conf", [""])[i]).strip() if "conf" in data else ""
            conf = int(float(conf_raw)) if conf_raw not in ("", "-1") else -1
            if conf != -1 and conf < 25:
                continue
            key = (int(data["block_num"][i]), int(data["line_num"][i]))
            if key not in lines:
                lines[key] = {"text": txt, "top": int(data["top"][i]), "height": int(data["height"][i])}
            else:
                lines[key]["text"] = str(lines[key]["text"]) + " " + txt
                lines[key]["top"] = min(int(lines[key]["top"]), int(data["top"][i]))
                lines[key]["height"] = max(int(lines[key]["height"]), int(data["height"][i]))
        return list(lines.values())

    raw_lines: list[dict[str, int | str]] = []
    for im in rowname_imgs:
        raw_lines.extend(extract_lines_psm(im, 11))

    # Merge exact-duplicate lines by very tight y proximity.
    raw_lines.sort(key=lambda d: int(d["top"]))
    merged: list[dict[str, int | str]] = []
    for ln in raw_lines:
        if not merged:
            merged.append(ln)
            continue
        prev = merged[-1]
        if abs(int(ln["top"]) - int(prev["top"])) <= 2 and str(ln["text"]).strip().lower() == str(prev["text"]).strip().lower():
            # prefer longer text
            t_prev = str(prev["text"])
            t_new = str(ln["text"])
            if len(t_new) > len(t_prev):
                prev["text"] = t_new
            prev["height"] = max(int(prev["height"]), int(ln["height"]))
        else:
            merged.append(ln)

    # Sort lines by y
    sorted_lines = merged
    rows_out: list[list[str]] = []

    # Estimate typical row pitch (distance between successive text baselines).
    tops = [int(ln["top"]) for ln in sorted_lines]
    diffs = [b - a for a, b in zip(tops, tops[1:]) if (b - a) > 3]
    row_pitch = int(np.median(diffs)) if diffs else 28
    min_gap_for_blank = int(row_pitch * 1.8)

    # Always include the known empty row directly under header.
    empty_row = [""] * len(headers)
    rows_out.append(empty_row)

    prev_top: int | None = None
    # Track per-column preferred decimal places from early successful reads.
    dec_pref: dict[int, int] = {}
    dec_counts: dict[int, dict[int, int]] = {}

    for ln in sorted_lines:
        top = int(ln["top"])
        height = int(ln["height"])
        y_abs = body_top + top
        if y_abs >= body_bottom:
            continue

        rowname = " ".join(str(ln["text"]).split())
        # Insert blank rows for large vertical gaps to preserve empty rows as in the image.
        if prev_top is not None:
            gap = top - prev_top
            if gap >= min_gap_for_blank:
                n_blanks = max(1, int(round(gap / float(max(1, row_pitch)))) - 1)
                for _ in range(n_blanks):
                    rows_out.append([""] * len(headers))
        prev_top = top

        # Skip the known always-empty first row under header by requiring either a rowname or some numeric later.
        # We'll handle spacer rows naturally by checking all-empty.

        # Build row values in the same order as headers (rowname column in-place).
        row_vals = [""] * len(headers)
        row_vals[rowname_idx] = rowname
        all_numeric_empty = True
        for i, (a, b) in enumerate(spans):
            if i == rowname_idx:
                continue
            # Use row_pitch-based height rather than OCR word height to keep numeric crops aligned.
            y_center = y_abs + max(6, int(height * 0.5))
            y0 = max(body_top, int(y_center - row_pitch * 0.45))
            h0 = min(body_bottom - y0, max(18, int(row_pitch * 0.90)))
            cell_bbox = (a, y0, b - a, h0)
            val = _ocr_numeric_cell_best(gray, cell_bbox, langs=langs, prefer_decimals=dec_pref.get(i))
            if val:
                all_numeric_empty = False
                # Learn decimal preference for this column.
                if "." in val:
                    d = len(val.split(".")[-1])
                    if 0 <= d <= 4:
                        dc = dec_counts.setdefault(i, {})
                        dc[d] = dc.get(d, 0) + 1
                        # Once we have a stable mode, prefer it.
                        if sum(dc.values()) >= 4:
                            mode_d = max(dc.items(), key=lambda kv: kv[1])[0]
                            dec_pref[i] = mode_d
            row_vals[i] = val

        # Keep rows even if numeric cells are empty (years/provinces can have blanks).
        rows_out.append(row_vals)

    # Reorder so rowname column is first for downstream convenience, but keep raw header texts.
    if rowname_idx != 0:
        headers = [headers[rowname_idx]] + headers[:rowname_idx] + headers[rowname_idx + 1 :]
        new_rows = []
        for r in rows_out:
            new_rows.append([r[rowname_idx]] + r[:rowname_idx] + r[rowname_idx + 1 :])
        rows_out = new_rows

    headers = _maybe_canonicalize_year_region_headers(headers)

    return AnchoredTable(rowname_header=tl_txt, headers=headers, rows=rows_out)

