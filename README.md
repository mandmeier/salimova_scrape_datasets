## salimova_scrape_datasets

Reproducible scraping + preprocessing pipelines for:

- **CARSS** (`data.carss.cn`): drug resistance tables (`publish/drug`) and indicator lineplots (`publish/indicator`).
- **National Bureau of Statistics (China) yearbooks** (`stats.gov.cn`): offline bundle for tables (HTML for 2005–2013; JPG for 2014–2025).

The repo is structured for **journal publication**: clear entrypoints, pinned dependencies, and outputs written to a single gitignored `artifacts/` folder.

### Quickstart (Docker + Make)

Build the container:

```bash
make build
```

Print environment versions (saved to `artifacts/env_versions.txt`):

```bash
make env-info
```

### CARSS (requires cookies)

CARSS is protected by a WAF. You must export fresh cookies from your browser after passing the human check.

- Put the cookies JSON at `artifacts/secrets/cookies.json` (this path is gitignored).

Scrape the **publish/drug** dataset:

```bash
make carss-drug YEAR=2024
```

Scrape the **publish/indicator** lineplot dataset:

```bash
make carss-indicator
```

Outputs (gitignored by default):

- `artifacts/carss/publish_drug/carss_drug_resistance.csv`
- `artifacts/carss/publish_indicator/carss_drug_resistance_lineplot.csv`

### Yearbook (stats.gov.cn)

2014–2025: index + download JPG table images:

```bash
make yearbook-2014 YEAR=2024
```

2005–2013: index + download HTML table pages for offline extraction:

```bash
make yearbook-2005 YEAR=2005
```

Outputs (gitignored by default) are written under:

- `artifacts/yearbook/2014-2025/<YEAR>/`
- `artifacts/yearbook/2005-2013/<YEAR>/`

### Notes on reproducibility

- The canonical environment is defined by `Dockerfile` + `requirements.txt`.
- Python code lives in `src/` and is imported via `PYTHONPATH=/app/src` inside the container.


