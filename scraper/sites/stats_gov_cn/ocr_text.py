from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class OcrTextResult:
    text: str


def ocr_text_tesseract(image_path: str, *, langs: str = "eng", psm: int = 6) -> OcrTextResult:
    """
    Text-only OCR using local Tesseract. Good enough for province detection.

    langs examples:
    - 'eng' (default)
    - 'chi_sim+eng' (if you have Chinese traineddata installed)
    """
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("pytesseract/Pillow missing. Install deps with `pip install -r requirements.txt`.") from e

    img = Image.open(image_path)
    # Basic preprocessing for scanned tables: grayscale + resize.
    img = img.convert("L")
    w, h = img.size
    # Tesseract works better when text height is not tiny; cap width to avoid extreme memory.
    target_w = 2200
    if w > target_w:
        scale = target_w / float(w)
        img = img.resize((target_w, int(h * scale)))
    cfg = f"--psm {psm}"
    txt = pytesseract.image_to_string(img, lang=langs, config=cfg)
    return OcrTextResult(text=txt or "")


def try_ocr_text(image_path: str, *, lang_candidates: Iterable[str]) -> OcrTextResult:
    """
    Try multiple tesseract language packs; returns first non-empty result.
    """
    best = OcrTextResult(text="")
    for langs in lang_candidates:
        try:
            res = ocr_text_tesseract(image_path, langs=langs)
        except Exception:
            continue
        if res.text and len(res.text.strip()) > len(best.text.strip()):
            best = res
    return best

