from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from typing import Dict, Iterable, Tuple


@dataclass(frozen=True)
class ParsedIndicator:
    antibiotic_cn: str
    antibiotic_en: str
    bacteria_cn: str
    bacteria_en_abbrev: str
    organism_code: str


_WS_RE = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").strip())


# Canonical mappings for the indicator dropdown codes we observed.
# This produces a stable, drug-resistance-style representation:
# <antibiotic>-resistant <bacteria>
INDICATOR_MAP: Dict[str, ParsedIndicator] = {
    "MRSA": ParsedIndicator(
        antibiotic_cn="甲氧西林",
        antibiotic_en="Methicillin",
        bacteria_cn="金黄色葡萄球菌",
        bacteria_en_abbrev="S. aureus",
        organism_code="sau",
    ),
    "MRCNS": ParsedIndicator(
        antibiotic_cn="甲氧西林",
        antibiotic_en="Methicillin",
        bacteria_cn="凝固酶阴性葡萄球菌",
        bacteria_en_abbrev="CoNS",
        organism_code="cns",
    ),
    "VREFA": ParsedIndicator(
        antibiotic_cn="万古霉素",
        antibiotic_en="Vancomycin",
        bacteria_cn="粪肠球菌",
        bacteria_en_abbrev="E. faecalis",
        organism_code="efa",
    ),
    "VREFM": ParsedIndicator(
        antibiotic_cn="万古霉素",
        antibiotic_en="Vancomycin",
        bacteria_cn="屎肠球菌",
        bacteria_en_abbrev="E. faecium",
        organism_code="efm",
    ),
    "PRSP(nm)-R": ParsedIndicator(
        antibiotic_cn="青霉素",
        antibiotic_en="Penicillin",
        bacteria_cn="肺炎链球菌",
        bacteria_en_abbrev="S. pneumoniae",
        organism_code="spn",
    ),
    "ERSP": ParsedIndicator(
        antibiotic_cn="红霉素",
        antibiotic_en="Erythromycin",
        bacteria_cn="肺炎链球菌",
        bacteria_en_abbrev="S. pneumoniae",
        organism_code="spn",
    ),
    "CRECO": ParsedIndicator(
        antibiotic_cn="碳青霉烯类",
        antibiotic_en="Carbapenems",
        bacteria_cn="大肠埃希菌",
        bacteria_en_abbrev="E. coli",
        organism_code="eco",
    ),
    "CtxCroREco": ParsedIndicator(
        antibiotic_cn="头孢噻肟/头孢曲松",
        antibiotic_en="Cefotaxime/Ceftriaxone",
        bacteria_cn="大肠埃希菌",
        bacteria_en_abbrev="E. coli",
        organism_code="eco",
    ),
    "QnREco": ParsedIndicator(
        antibiotic_cn="喹诺酮类",
        antibiotic_en="Quinolones",
        bacteria_cn="大肠埃希菌",
        bacteria_en_abbrev="E. coli",
        organism_code="eco",
    ),
    "CRKPN": ParsedIndicator(
        antibiotic_cn="碳青霉烯类",
        antibiotic_en="Carbapenems",
        bacteria_cn="肺炎克雷伯菌",
        bacteria_en_abbrev="K. pneumoniae",
        organism_code="kpn",
    ),
    "CtxCroRKpn": ParsedIndicator(
        antibiotic_cn="头孢噻肟/头孢曲松",
        antibiotic_en="Cefotaxime/Ceftriaxone",
        bacteria_cn="肺炎克雷伯菌",
        bacteria_en_abbrev="K. pneumoniae",
        organism_code="kpn",
    ),
    "CRPAE": ParsedIndicator(
        antibiotic_cn="碳青霉烯类",
        antibiotic_en="Carbapenems",
        bacteria_cn="铜绿假单胞菌",
        bacteria_en_abbrev="P. aeruginosa",
        organism_code="pae",
    ),
    "CRABA": ParsedIndicator(
        antibiotic_cn="碳青霉烯类",
        antibiotic_en="Carbapenems",
        bacteria_cn="鲍曼不动杆菌",
        bacteria_en_abbrev="A. baumannii",
        organism_code="aba",
    ),
}


def parse_indicator(indicator_code: str, bacteria_species_cn: str) -> ParsedIndicator:
    code = _norm(indicator_code)
    if code in INDICATOR_MAP:
        return INDICATOR_MAP[code]
    # Best-effort fallback: keep organism_code as indicator code, and try to keep bacteria CN.
    return ParsedIndicator(
        antibiotic_cn="",
        antibiotic_en="",
        bacteria_cn=_norm(bacteria_species_cn),
        bacteria_en_abbrev="",
        organism_code=code,
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Normalize CARSS indicator lineplot CSV to match drug format.")
    ap.add_argument("--in", dest="inp", default="carss_drug_resistance_lineplot.csv")
    ap.add_argument("--out", dest="out", default="carss_drug_resistance_lineplot__normalized.csv")
    args = ap.parse_args(argv)

    with open(args.inp, "r", encoding="utf-8", newline="") as fin:
        reader = csv.DictReader(fin)
        if not reader.fieldnames:
            raise RuntimeError("Input CSV has no header.")

        out_fields = [
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

        with open(args.out, "w", encoding="utf-8", newline="") as fout:
            w = csv.DictWriter(fout, fieldnames=out_fields)
            w.writeheader()

            n = 0
            for row in reader:
                n += 1
                indicator = _norm(row.get("organism_code") or "")
                label_cn = _norm(row.get("bacteria_species") or "")
                parsed = parse_indicator(indicator, label_cn)

                # Convert to the same semantics as carss_drug_resistance.csv
                # - bacteria_species: bacteria only (English abbrev)
                # - organism_code: bacteria code (sau/eco/...) where known
                # - drug_full_name_*: antibiotic name (CN/EN)
                out_row = dict(row)
                out_row["bacteria_species"] = parsed.bacteria_en_abbrev or parsed.bacteria_cn
                out_row["organism_code"] = parsed.organism_code
                out_row["drug_full_name_cn"] = parsed.antibiotic_cn
                out_row["drug_full_name_en"] = parsed.antibiotic_en
                # leave drug_code empty for now
                out_row["drug_code"] = ""

                # Ensure numeric formatting stays parseable
                w.writerow({k: out_row.get(k, "") for k in out_fields})

    print(f"OK wrote {args.out}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))

