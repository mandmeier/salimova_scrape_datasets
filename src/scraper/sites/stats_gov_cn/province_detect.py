from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ProvinceDetection:
    matched: list[str]

    @property
    def match_count(self) -> int:
        return len(self.matched)


def load_provinces_cn(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [ln.strip() for ln in f.readlines() if ln.strip()]


def detect_provinces_in_text(text: str, provinces_cn: Iterable[str]) -> ProvinceDetection:
    matched = []
    for p in provinces_cn:
        if p and p in text:
            matched.append(p)
    return ProvinceDetection(matched=matched)


def get_default_province_list_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "assets", "provinces_cn.txt")


def get_default_province_en_list_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "assets", "provinces_en.txt")

