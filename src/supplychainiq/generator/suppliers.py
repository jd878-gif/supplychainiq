"""Create suppliers and assign every item to one supplier in its department."""

import numpy as np
import pandas as pd


def build_suppliers(
    meta: pd.DataFrame, avg_price: pd.Series, cfg: dict, rng: np.random.Generator
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (suppliers, products).

    suppliers: supplier_id, dept_id, cat_id, reliability_tier, base_lead_time_days, review_weekday
    products:  item_id, dept_id, cat_id, supplier_id, unit_cost
    """
    per_dept = cfg["suppliers"]["per_department"]
    rows = []
    for dept in sorted(meta["dept_id"].unique()):
        cat = dept.split("_")[0]
        lo, hi = cfg["lead_time_days"][cat]
        for k in range(1, per_dept + 1):
            rows.append(
                {
                    "supplier_id": f"SUP-{dept}-{k:02d}",
                    "dept_id": dept,
                    "cat_id": cat,
                    "base_lead_time_days": int(rng.integers(lo, hi + 1)),
                    "review_weekday": int(rng.integers(0, 7)),  # Monday = 0
                }
            )
    suppliers = pd.DataFrame(rows)

    n_unreliable = round(cfg["suppliers"]["unreliable_share"] * len(suppliers))
    suppliers["reliability_tier"] = "reliable"
    if n_unreliable:
        idx = rng.choice(len(suppliers), size=n_unreliable, replace=False)
        suppliers.loc[idx, "reliability_tier"] = "unreliable"

    by_dept = suppliers.groupby("dept_id")["supplier_id"].apply(list)
    products = meta[["item_id", "dept_id", "cat_id"]].drop_duplicates("item_id").copy()
    products["supplier_id"] = [
        by_dept[d][int(rng.integers(len(by_dept[d])))] for d in products["dept_id"]
    ]

    price = products["item_id"].map(avg_price)
    price = price.fillna(avg_price.median() if len(avg_price) else 1.0)
    lo, hi = cfg["cost_ratio"]
    products["unit_cost"] = (price * rng.uniform(lo, hi, len(products))).round(2)

    cols = [
        "supplier_id", "dept_id", "cat_id", "reliability_tier",
        "base_lead_time_days", "review_weekday",
    ]
    return suppliers[cols], products.reset_index(drop=True)
