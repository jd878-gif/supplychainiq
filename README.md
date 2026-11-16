# SupplyChainIQ

Inventory, supplier and demand-forecasting lakehouse on **Azure Databricks**.

**Business question:** every morning, which items will run out before the next delivery arrives,
and which suppliers cause it?

## Status
Phase 0: setup and design (in progress)

## Data
- **Demand:** M5 (Walmart) daily unit sales, real data, 2-year window.
- **Supply side:** generated suppliers, purchase orders, shipments and inventory, with lead times
  calibrated on the DataCo supply chain dataset and faults injected and tracked in a ground-truth table.

## Architecture
Bronze (Auto Loader) → Silver (Lakeflow pipeline, expectations, SCD2) → Gold (facts, dimensions,
supplier scorecard) → demand forecast (MLflow) → stockout risk → Databricks SQL dashboard.
Unity Catalog governs every layer. Deployed with Databricks Asset Bundles + GitHub Actions.

## Local setup
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
bash scripts/download_data.sh
pytest -q && ruff check .
```

## Decisions
See [docs/adr](docs/adr).
