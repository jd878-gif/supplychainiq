"""Shared helpers for generator tests: small synthetic M5-like data."""

import numpy as np
import pandas as pd

from supplychainiq.generator.load_m5 import M5Data
from supplychainiq.generator.simulate import simulate
from supplychainiq.generator.suppliers import build_suppliers


def make_data(n_items=40, stores=("CA_1", "CA_2"), days=120, seed=0) -> M5Data:
    rng = np.random.default_rng(seed)
    depts = ["FOODS_1", "HOUSEHOLD_1", "HOBBIES_1"]
    rows = []
    for i in range(n_items):
        dept = depts[i % len(depts)]
        for st in stores:
            item = f"{dept}_{i:03d}"
            rows.append(
                {"id": f"{item}_{st}", "item_id": item, "dept_id": dept,
                 "cat_id": dept.split("_")[0], "store_id": st, "state_id": "CA"}
            )
    meta = pd.DataFrame(rows)
    means = rng.uniform(0.2, 6, len(meta))
    demand = rng.poisson(means, size=(days, len(meta))).astype(np.int32)
    hist = rng.poisson(means, size=(120, len(meta))).astype(np.int16)
    return M5Data(
        demand=demand,
        hist_demand=hist,
        meta=meta,
        dates=pd.date_range("2015-01-01", periods=days, freq="D"),
        avg_price=pd.Series({r: 5.0 for r in meta["item_id"].unique()}),
    )


def run(cfg, data, seed=1):
    rng = np.random.default_rng(seed)
    suppliers, products = build_suppliers(data.meta, data.avg_price, cfg, rng)
    return suppliers, products, simulate(data, suppliers, products, cfg, rng)
