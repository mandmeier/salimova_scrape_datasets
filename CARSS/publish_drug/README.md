## CARSS publish/drug

Entry point:

```bash
make carss-drug YEAR=2024
```

This target writes to:

- `artifacts/carss/publish_drug/carss_drug_resistance.csv`

CARSS is protected by a WAF. Provide a fresh cookie export at:

- `artifacts/secrets/cookies.json`

