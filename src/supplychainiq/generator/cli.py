"""Run the generator.

Examples (from the repo root, venv active):
    python -m supplychainiq.generator.cli --states CA --days 60   # quick check
    python -m supplychainiq.generator.cli                          # full run from config
"""

import argparse
import shutil
import time
from pathlib import Path

import numpy as np
import pandas as pd

from supplychainiq.generator.config import DEFAULT_CONFIG, load_config
from supplychainiq.generator.faults import (
    apply_supplier_switches,
    ground_truth,
    plan_faults,
    pos_for_discontinued,
)
from supplychainiq.generator.load_m5 import load_m5
from supplychainiq.generator.simulate import simulate
from supplychainiq.generator.suppliers import build_suppliers
from supplychainiq.generator.validate import check_criteria, supplier_scorecard, tier_summary
from supplychainiq.generator.writers import (
    write_ground_truth,
    write_landing_files,
    write_postgres_seed,
)


def find_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "pyproject.toml").exists():
            return p
    raise FileNotFoundError("Run from inside the repo (no pyproject.toml found above cwd)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SupplyChainIQ supply-side generator")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--states", nargs="+", help="Override states, e.g. --states CA")
    parser.add_argument("--days", type=int, help="Simulate only the first N days")
    parser.add_argument("--no-save", action="store_true", help="Print results only")
    parser.add_argument(
        "--write-files", action="store_true",
        help="Also write landing files, Postgres seed and ground truth (with faults)",
    )
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if args.states:
        cfg["data"]["states"] = args.states
    root = find_root(Path.cwd())
    rng = np.random.default_rng(cfg["seed"])

    t0 = time.perf_counter()
    data = load_m5(cfg, root)
    if args.days:
        data = data.head_days(args.days)
        drift = cfg["faults"]["schema_drift"]
        if pd.Timestamp(drift["start_date"]) > data.dates[-1]:
            # short test run: move the drift date to the middle of the shortened window
            drift["start_date"] = str(data.dates[len(data.dates) // 2].date())
            print(f"--days run: schema drift moved to {drift['start_date']} for this run only")
    print(f"Loaded {data.demand.shape[1]:,} series x {data.demand.shape[0]} days "
          f"({data.dates[0].date()} to {data.dates[-1].date()}) in {time.perf_counter()-t0:.1f}s")

    suppliers, products = build_suppliers(data.meta, data.avg_price, cfg, rng)
    t1 = time.perf_counter()
    result = simulate(data, suppliers, products, cfg, rng)
    print(f"Simulated in {time.perf_counter()-t1:.1f}s: "
          f"{len(result.purchase_orders):,} POs, {len(result.shipments):,} shipments")

    summary = tier_summary(result, data, cfg)
    card = supplier_scorecard(result, suppliers)
    criteria = check_criteria(summary, card, cfg)
    print("\nBy tier:\n", summary.to_string(index=False))
    print("\nWorst 8 suppliers by on-time rate:\n", card.head(8).to_string(index=False))
    print("\nValidation criteria:\n", criteria.to_string(index=False))
    print("\nALL CRITERIA PASSED" if criteria["passed"].all() else "\nSOME CRITERIA FAILED")

    if args.no_save:
        return
    out = root / cfg["data"]["output_dir"]
    (out / "tables").mkdir(parents=True, exist_ok=True)
    suppliers.to_parquet(out / "tables" / "suppliers.parquet", index=False)
    products.to_parquet(out / "tables" / "products.parquet", index=False)
    result.purchase_orders.to_parquet(out / "tables" / "purchase_orders.parquet", index=False)
    result.shipments.to_parquet(out / "tables" / "shipments.parquet", index=False)
    result.po_truth.to_parquet(out / "tables" / "po_truth.parquet", index=False)
    result.series_supplier.to_parquet(out / "tables" / "series.parquet", index=False)
    np.savez_compressed(
        out / "daily_arrays.npz",
        dates=data.dates.strftime("%Y-%m-%d").to_numpy(),
        demand=data.demand, sold=result.sold, lost=result.lost,
        on_hand=result.on_hand, received=result.received,
    )
    summary.to_csv(out / "validation_by_tier.csv", index=False)
    criteria.to_csv(out / "validation_criteria.csv", index=False)
    card.to_csv(out / "supplier_scorecard.csv", index=False)
    print(f"\nSaved tables and arrays to {out}")

    if args.write_files:
        t2 = time.perf_counter()
        for sub in ("landing", "postgres_seed", "ground_truth"):
            shutil.rmtree(out / sub, ignore_errors=True)  # no stale files from earlier runs
        plan = plan_faults(data, result, products, suppliers, cfg, rng)
        purchase_orders = apply_supplier_switches(result.purchase_orders, plan.product_changes)
        disc_pos = pos_for_discontinued(purchase_orders, plan.product_changes)
        dups = write_landing_files(out, data, result, plan, cfg, rng)
        write_postgres_seed(
            out, suppliers, products, purchase_orders, result.shipments, plan, data.dates[0]
        )
        faults = ground_truth(plan, dups, disc_pos)
        write_ground_truth(out, faults, data, result)
        print(f"\nWrote landing files + seed + ground truth in {time.perf_counter()-t2:.0f}s")
        print("Injected faults:\n", faults["fault_type"].value_counts().to_string())


if __name__ == "__main__":
    main()
