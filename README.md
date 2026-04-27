## Scraping toolkit

This repo contains a small, reusable scraping toolkit with **site plugins**.

### Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run CARSS (drug resistance)

CARSS is protected by a WAF and requires browser cookies.

```bash
source .venv/bin/activate
python -m scraper run carss_drug --cookies cookies.json --out carss_drug_resistance.csv
```

Optional debug:

```bash
python -m scraper run carss_drug --cookies cookies.json --out /tmp/sample.csv --limit-combos 2
```

### Run stats.gov.cn yearbook discovery/download

This plugin discovers and downloads the JPG tables for a given year.

Discover:

```bash
source .venv/bin/activate
python -m scraper run stats_gov_cn --year 2024 --out out/stats_gov_cn/2024
```

Download discovered images:

```bash
python -m scraper run stats_gov_cn --year 2024 --out out/stats_gov_cn/2024 --download
```

Artifacts:
- `discovery.json`: discovery metadata
- `manifest.jsonl`: one line per downloaded image (urls, checksum, size, filename)

