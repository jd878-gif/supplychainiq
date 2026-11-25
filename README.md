# SupplyChainIQ

Production-style lakehouse on **Azure Databricks** that answers: *which suppliers cause stockouts, and how much revenue do we lose?*

Real Walmart demand (M5) drives a simulated supply chain (suppliers, purchase orders, shipments, inventory)
with **injected faults and a ground-truth table**, so every data-quality rule and estimate is graded, not assumed.

## Results so far
| Area | Result (graded against ground truth) |
|---|---|
| Bronze ingestion (Auto Loader) | Incremental + exactly-once; schema drift (`carrier_code`) evolved automatically |
| POS deduplication | 14,765 / 14,765 injected duplicates removed, 0 remaining |
| Inventory DQ | 31 / 31 bad rows quarantined, 0 false alarms |
| Completeness monitor | 7 / 7 late files caught; holidays separated; 1 real Walmart store anomaly surfaced |
| Postgres CDC (query-based) + SCD2 | Silver POs = source (1,059,859); received POs = shipment files exactly |
| Supplier scorecard | Unreliable suppliers found blind: OTIF 0.31 vs 0.91; 3x lost-sales rate |
| Lost-sales estimate (censored demand) | EM estimator captures ~70% of true loss (WAPE 0.30); reported as a lower bound |

## Architecture (current)
```
Landing files (ADLS) --Auto Loader--> Bronze --DQ/dedup/quarantine--> Silver --> Gold (facts, KPIs, supplier scorecard)
Postgres (ops DB) --Lakehouse Federation + watermark CDC--> Bronze CDC --> Silver (SCD2 products, POs)
Governed by Unity Catalog; serverless compute.
```

## Roadmap
- [x] Generator, fault injection, ground truth (local, tested, CI)
- [x] Azure + Unity Catalog setup; Bronze; Silver + DQ; Gold; Postgres CDC + SCD2
- [ ] Lakeflow Job orchestration + alerts
- [ ] AI/BI dashboard
- [ ] Declarative pipeline (incremental)
- [ ] Asset Bundles + CI/CD (dev/prod)
- [ ] Demand forecast + MLflow

## Repo layout
- `src/supplychainiq/generator/`: simulation + fault injection (tested)
- `scripts/`: data download, landing upload, Postgres replay
- `notebooks/databricks/`: Bronze, Silver, Gold, CDC notebooks
- `docs/`: ADRs, tuning log, data profile

## Local setup
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
bash scripts/download_data.sh
pytest -q && ruff check .
```