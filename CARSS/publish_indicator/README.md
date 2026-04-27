## CARSS publish/indicator

Entry point:

```bash
make carss-indicator
```

This target writes to:

- `artifacts/carss/publish_indicator/carss_drug_resistance_lineplot.csv`

Config files (tracked):

- `CARSS/publish_indicator/configs/layers_options.json`
- `CARSS/publish_indicator/configs/indicator_options.json`

CARSS is protected by a WAF. Provide a fresh cookie export at:

- `artifacts/secrets/cookies.json`

