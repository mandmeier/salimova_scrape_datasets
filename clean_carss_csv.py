from __future__ import annotations

import argparse
import csv
import re
from typing import Dict


PROVINCE_CN_TO_EN: Dict[str, str] = {
    "全国": "National",
    "北京": "Beijing",
    "天津": "Tianjin",
    "河北": "Hebei",
    "山西": "Shanxi",
    "内蒙古": "Inner Mongolia",
    "辽宁": "Liaoning",
    "吉林": "Jilin",
    "黑龙江": "Heilongjiang",
    "上海": "Shanghai",
    "江苏": "Jiangsu",
    "浙江": "Zhejiang",
    "安徽": "Anhui",
    "福建": "Fujian",
    "江西": "Jiangxi",
    "山东": "Shandong",
    "河南": "Henan",
    "湖北": "Hubei",
    "湖南": "Hunan",
    "广东": "Guangdong",
    "广西": "Guangxi",
    "海南": "Hainan",
    "重庆": "Chongqing",
    "四川": "Sichuan",
    "贵州": "Guizhou",
    "云南": "Yunnan",
    "西藏": "Tibet",
    "陕西": "Shaanxi",
    "甘肃": "Gansu",
    "青海": "Qinghai",
    "宁夏": "Ningxia",
    "新疆": "Xinjiang",
}


ORGANISM_TO_EN: Dict[str, str] = {
    "sau": "S. aureus",
    "eco": "E. coli",
    "kpn": "K. pneumoniae",
    "aba": "A. baumannii",
    "pae": "P. aeruginosa",
}

STANDARD_ANTIBIOTICS = [
    "4-Epichlortetracycline",
    "Acetylspiramycin",
    "Amoxicillin",
    "Ampicillin",
    "Anhydrochlortetracycline",
    "Apo-Oxytetracycline",
    "Aureomycin",
    "Azithromycin",
    "Carbadox",
    "Cefazolin",
    "Cefotaxime",
    "Cefoxitin",
    "Ceftriaxone",
    "Cephalexin",
    "Chloramphenicol",
    "Chloromycetin",
    "Chlorotetracycline",
    "Ciprofloxacin",
    "Clarithromycin",
    "Clindamycin",
    "Cloxacillin",
    "Danofloxacin",
    "Dehydroerythromycin",
    "Demeclocycline",
    "Difloxacin",
    "Doxycycline",
    "Enoxacin",
    "Enrofloxacin",
    "Epianhydrotetracycline",
    "Epioxytetracycline",
    "Epitetracycline",
    "Erythromycin",
    "Erythromycin A dihydrate",
    "Erythromycin-H2O",
    "Fleroxacin",
    "Florfenicol",
    "Flumequine",
    "Gatifloxacin",
    "Gentamicin",
    "Isochlortetracycline",
    "Josamycin",
    "Kitasamycin",
    "Leucomycin",
    "Levofloxacin",
    "Lincomycin",
    "Lomefloxacin",
    "Marbofloxacin",
    "Mecillinam",
    "Methacycline",
    "Minocycline",
    "Monensin",
    "Moxifloxacin",
    "Nadifloxacin",
    "Nalidixic acid",
    "Narasin",
    "Nitrofurantoin",
    "Norfloxacin",
    "Ofloxacin",
    "Oleandomycin Phosphate",
    "Ormetoprim",
    "Oxacillin",
    "Oxytetracycline",
    "Pefloxacin",
    "Penicillin G",
    "Penicillin V",
    "Pipemidic Acid",
    "Rifampicin",
    "Roxithromycin",
    "Roxithromycin-H2O",
    "Sarafloxacin",
    "Sarmoxicillin",
    "Sparfloxacin",
    "Spectinomycin",
    "Spiramycin",
    "Streptomycin",
    "Sulfabenzamide",
    "Sulfacetamide",
    "Sulfachinoxalin",
    "Sulfachinoxaline",
    "Sulfachloropyridazine",
    "Sulfadiazine",
    "Sulfadimethazine",
    "Sulfadimethoxine",
    "Sulfadimethoxine sodium salt",
    "Sulfadimethoxypyrimidine",
    "Sulfadimidine",
    "Sulfadimoxine",
    "Sulfadoxine",
    "Sulfafurazole",
    "Sulfaguanidine",
    "Sulfamerazine",
    "Sulfameter",
    "Sulfamethazine",
    "Sulfamethazole",
    "Sulfamethizole",
    "Sulfamethoxazole",
    "Sulfamethoxydiazine",
    "Sulfamethoxypyridazine",
    "Sulfamonomethoxine",
    "Sulfamoxole",
    "Sulfanilamide",
    "Sulfanitran",
    "Sulfaphenazole",
    "Sulfapyridine",
    "Sulfaquinoxaline",
    "Sulfathiazole",
    "Sulfisomidine",
    "Sulfisoxazole",
    "Tetracycline",
    "Tetracyclines (total)",
    "Tosufloxacin tosylate",
    "Total Antibiotics",
    "Total Quinolones (QNs)",
    "Total Tetracyclines (TCs)",
    "Trimethoprim",
    "Tylosin",
    "Vancomycin",
]


def _norm_antibiotic_name(s: str) -> str:
    # Lowercase and strip non-alphanumerics to make matching resilient.
    return re.sub(r"[^a-z0-9]+", "", s.lower())


_STANDARD_BY_NORM = {_norm_antibiotic_name(x): x for x in STANDARD_ANTIBIOTICS}

# Minimal synonyms we already observe in CARSS drug_full_name.
_SYNONYMS = {
    _norm_antibiotic_name("Rifampin"): "Rifampicin",
}


def standardize_antibiotic(drug_full_name: str) -> str:
    """
    Returns standardized antibiotic identifier(s) based on STANDARD_ANTIBIOTICS.
    - If name contains '/', each component is standardized and joined by '/'.
    - If component can't be standardized, it is kept as-is.
    """
    raw = (drug_full_name or "").strip()
    if not raw:
        return ""

    parts = [p.strip() for p in raw.split("/") if p.strip()]
    out_parts = []
    for part in parts:
        n = _norm_antibiotic_name(part)
        if n in _SYNONYMS:
            out_parts.append(_SYNONYMS[n])
            continue
        std = _STANDARD_BY_NORM.get(n)
        out_parts.append(std or part)

    # Preserve the slash structure if it was a combo, otherwise just single name.
    return "/".join(out_parts)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Create cleaned CARSS drug resistance CSV.")
    p.add_argument("--in", dest="inp", default="carss_drug_resistance.csv")
    p.add_argument("--out", dest="out", default="carss_drug_resistance__cleaned.csv")
    args = p.parse_args(argv)

    with open(args.inp, "r", encoding="utf-8", newline="") as fin:
        reader = csv.DictReader(fin)
        if not reader.fieldnames:
            raise RuntimeError("Input CSV has no header.")

        required_in_cols = {
            "year",
            "province",
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
        }
        missing = sorted(required_in_cols - set(reader.fieldnames))
        if missing:
            raise RuntimeError(f"Input CSV missing required columns: {missing}")

        out_fields = [
            "year",
            "province",
            "province_cn",
            "bacteria_species",
            "bacteria_species_cn",
            "drug_full_name",
            "drug_full_name_cn",
            "drug_code",
            "total_n_strains",
            "resistant_percent",
            "resistant_n_strains",
            "intermediate_percent",
            "intermediate_n_strains",
            "sensitive_percent",
            "sensitive_n_strains",
        ]

        unmapped_provinces = set()
        unmapped_organisms = set()
        row_count = 0

        with open(args.out, "w", encoding="utf-8", newline="") as fout:
            writer = csv.DictWriter(fout, fieldnames=out_fields)
            writer.writeheader()

            for row in reader:
                row_count += 1

                province_cn = (row.get("province") or "").strip()
                organism_code = (row.get("organism_code") or "").strip()
                bacteria_cn = (row.get("bacteria_species") or "").strip()

                province_en = PROVINCE_CN_TO_EN.get(province_cn, "")
                if not province_en:
                    unmapped_provinces.add(province_cn)

                bacteria_en = ORGANISM_TO_EN.get(organism_code, "")
                if not bacteria_en:
                    unmapped_organisms.add(organism_code)

                drug_full_name = (row.get("drug_full_name_en") or "").strip()
                drug_full_name_cn = (row.get("drug_full_name_cn") or "").strip()

                out_row = {
                    "year": (row.get("year") or "").strip(),
                    "province": province_en,
                    "province_cn": province_cn,
                    "bacteria_species": bacteria_en,
                    "bacteria_species_cn": bacteria_cn,
                    "drug_full_name": drug_full_name,
                    "drug_full_name_cn": drug_full_name_cn,
                    "drug_code": (row.get("drug_code") or "").strip(),
                    "total_n_strains": (row.get("total_n_strains") or "").strip(),
                    "resistant_percent": (row.get("resistant_percent") or "").strip(),
                    "resistant_n_strains": (row.get("resistant_n_strains") or "").strip(),
                    "intermediate_percent": (row.get("intermediate_percent") or "").strip(),
                    "intermediate_n_strains": (row.get("intermediate_n_strains") or "").strip(),
                    "sensitive_percent": (row.get("sensitive_percent") or "").strip(),
                    "sensitive_n_strains": (row.get("sensitive_n_strains") or "").strip(),
                }

                writer.writerow(out_row)

    if unmapped_provinces:
        raise RuntimeError(f"Unmapped provinces: {sorted(unmapped_provinces)}")
    if unmapped_organisms:
        raise RuntimeError(f"Unmapped organism codes: {sorted(unmapped_organisms)}")

    print(f"OK wrote {row_count} rows -> {args.out}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))

