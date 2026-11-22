"""Plan the faults the pipeline must catch, and record them as ground truth.

Faults are PLANNED here (what, where, when) and APPLIED by writers.py while the
daily files are written. Every injected fault gets an ID in ground_truth/faults.csv,
so later we can report "the pipeline caught N of N".

Fault types:
  duplicate_pos_event      POS event sent twice (same file or next day's file)
  late_file                POS store-day file lands 2-3 days after its business date
  negative_inventory       snapshot on_hand < 0
  impossible_inventory     snapshot on_hand far above any plausible stock level
  schema_drift             shipments CSV gains a new column from a given date
  cost_change              products.unit_cost changes (SCD2)
  discontinued             products.status active -> discontinued (SCD2)
  supplier_switch          products.supplier_id changes (SCD2); later POs use the new supplier
  po_for_discontinued      business-rule violation that follows from the above
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from supplychainiq.generator.load_m5 import M5Data
from supplychainiq.generator.simulate import SimResult


@dataclass
class FaultPlan:
    late_files: pd.DataFrame        # store_id, event_date, delay_days, arrival_date
    bad_inventory: pd.DataFrame     # day_idx, series_idx, date, store_id, item_id, true, bad, kind
    schema_drift_date: pd.Timestamp | None
    schema_drift_column: str | None
    product_changes: pd.DataFrame   # change_id, item_id, change_type, field, old, new, date
    duplicate_rate: float
    duplicate_next_file_share: float


def plan_faults(
    data: M5Data,
    result: SimResult,
    products: pd.DataFrame,
    suppliers: pd.DataFrame,
    cfg: dict,
    rng: np.random.Generator,
) -> FaultPlan:
    fc = cfg["faults"]
    T = len(data.dates)
    warmup = min(cfg["window"]["warmup_days"], T - 1)

    # Late POS files: distinct (store, day) pairs, spread across the window
    lf = fc["late_files"]
    lo, hi = lf["delay_days"]
    stores = np.array(sorted(data.meta["store_id"].unique()))
    last_ok = max(warmup + 1, T - hi)
    n_late = min(lf["count"], len(stores) * (last_ok - warmup))
    pairs = set()
    while len(pairs) < n_late:
        pairs.add((str(rng.choice(stores)), int(rng.integers(warmup, last_ok))))
    late = pd.DataFrame(sorted(pairs), columns=["store_id", "day_idx"])
    late["event_date"] = data.dates[late["day_idx"]]
    late["delay_days"] = rng.integers(lo, hi + 1, len(late))
    late["arrival_date"] = late["event_date"] + pd.to_timedelta(late["delay_days"], unit="D")

    # Bad inventory values
    n_bad = fc["bad_inventory"]["count"]
    N = data.demand.shape[1]
    days = rng.integers(warmup, T, n_bad)
    idx = rng.integers(0, N, n_bad)
    kind = np.where(np.arange(n_bad) % 2 == 0, "negative_inventory", "impossible_inventory")
    true_val = result.on_hand[days, idx]
    bad_val = np.where(
        kind == "negative_inventory",
        -rng.integers(1, 50, n_bad),
        result.order_up_to[idx] * 20 + 1000,
    )
    bad = pd.DataFrame(
        {
            "day_idx": days, "series_idx": idx, "date": data.dates[days],
            "store_id": data.meta["store_id"].to_numpy()[idx],
            "item_id": data.meta["item_id"].to_numpy()[idx],
            "true_value": true_val, "bad_value": bad_val, "kind": kind,
        }
    ).drop_duplicates(["day_idx", "series_idx"]).reset_index(drop=True)

    # Schema drift
    sd = fc.get("schema_drift") or {}
    drift_date = pd.Timestamp(sd["start_date"]) if sd.get("start_date") else None
    if drift_date is not None and not (data.dates[0] <= drift_date <= data.dates[-1]):
        raise ValueError(
            f"schema_drift.start_date {drift_date.date()} is outside the window "
            f"{data.dates[0].date()} to {data.dates[-1].date()}"
        )

    changes = _plan_product_changes(data, products, suppliers, fc["product_updates"], rng)
    return FaultPlan(
        late_files=late,
        bad_inventory=bad,
        schema_drift_date=drift_date,
        schema_drift_column=sd.get("new_column"),
        product_changes=changes,
        duplicate_rate=fc["duplicate_pos_rate"],
        duplicate_next_file_share=fc["duplicate_next_file_share"],
    )


def _plan_product_changes(data, products, suppliers, pu, rng) -> pd.DataFrame:
    T = len(data.dates)
    rows = []
    prod = products.set_index("item_id")
    items = products["item_id"].to_numpy()

    # Cost changes: anywhere in the window
    for item in rng.choice(items, size=min(pu["cost_changes"], len(items)), replace=False):
        old = float(prod.loc[item, "unit_cost"])
        new = round(old * float(rng.uniform(0.90, 1.15)), 2)
        rows.append((item, "cost_change", "unit_cost", old, new, int(rng.integers(1, T))))

    # Discontinued: in the last 40% of the window
    for item in rng.choice(items, size=min(pu["discontinued"], len(items)), replace=False):
        rows.append(
            (item, "discontinued", "status", "active", "discontinued",
             int(rng.integers(int(T * 0.6), T)))
        )

    # Supplier switches: same department AND same tier, so simulated delays stay consistent
    sup = suppliers.set_index("supplier_id")
    siblings = {
        sid: [
            o for o in suppliers["supplier_id"]
            if o != sid
            and sup.loc[o, "dept_id"] == sup.loc[sid, "dept_id"]
            and sup.loc[o, "reliability_tier"] == sup.loc[sid, "reliability_tier"]
        ]
        for sid in suppliers["supplier_id"]
    }
    eligible = [i for i in items if siblings[prod.loc[i, "supplier_id"]]]
    n_sw = min(pu["supplier_switches"], len(eligible))
    for item in rng.choice(eligible, size=n_sw, replace=False) if n_sw else []:
        old = prod.loc[item, "supplier_id"]
        new = siblings[old][int(rng.integers(len(siblings[old])))]
        rows.append((item, "supplier_switch", "supplier_id", old, new, int(rng.integers(1, T))))

    df = pd.DataFrame(
        rows, columns=["item_id", "change_type", "field", "old_value", "new_value", "day_idx"]
    )
    df["effective_date"] = pd.DatetimeIndex(data.dates[df["day_idx"].to_numpy(dtype=int)])
    df = df.sort_values(["day_idx", "item_id"]).reset_index(drop=True)
    df.insert(0, "change_id", [f"CHG-{i:05d}" for i in range(1, len(df) + 1)])
    return df


def apply_supplier_switches(purchase_orders: pd.DataFrame, changes: pd.DataFrame) -> pd.DataFrame:
    """POs ordered on/after a switch date are placed with the new supplier."""
    po = purchase_orders.copy()
    for _, c in changes[changes["change_type"] == "supplier_switch"].iterrows():
        mask = (po["item_id"] == c["item_id"]) & (po["order_date"] >= c["effective_date"])
        po.loc[mask, "supplier_id"] = c["new_value"]
    return po


def pos_for_discontinued(purchase_orders: pd.DataFrame, changes: pd.DataFrame) -> pd.DataFrame:
    """POs placed for a product on/after its discontinuation date (rule violations)."""
    disc = changes.loc[changes["change_type"] == "discontinued", ["item_id", "effective_date"]]
    po = purchase_orders.merge(disc, on="item_id")
    return po[po["order_date"] >= po["effective_date"]]


def ground_truth(
    plan: FaultPlan, duplicates: pd.DataFrame, discontinued_pos: pd.DataFrame
) -> pd.DataFrame:
    """One row per injected fault."""
    parts = [
        pd.DataFrame(
            {
                "fault_type": "duplicate_pos_event", "target": "pos",
                "date": duplicates["event_date"], "store_id": duplicates["store_id"],
                "item_id": duplicates["item_id"],
                "detail": "event_id=" + duplicates["event_id"]
                + " resent_in=" + duplicates["file_date"].dt.strftime("%Y-%m-%d"),
            }
        ),
        pd.DataFrame(
            {
                "fault_type": "late_file", "target": "pos",
                "date": plan.late_files["event_date"], "store_id": plan.late_files["store_id"],
                "item_id": None,
                "detail": "arrives=" + plan.late_files["arrival_date"].dt.strftime("%Y-%m-%d"),
            }
        ),
        pd.DataFrame(
            {
                "fault_type": plan.bad_inventory["kind"], "target": "inventory",
                "date": plan.bad_inventory["date"], "store_id": plan.bad_inventory["store_id"],
                "item_id": plan.bad_inventory["item_id"],
                "detail": "true=" + plan.bad_inventory["true_value"].astype(str)
                + " written=" + plan.bad_inventory["bad_value"].astype(str),
            }
        ),
        pd.DataFrame(
            {
                "fault_type": plan.product_changes["change_type"], "target": "products",
                "date": plan.product_changes["effective_date"], "store_id": None,
                "item_id": plan.product_changes["item_id"],
                "detail": plan.product_changes["field"] + ": "
                + plan.product_changes["old_value"].astype(str) + " -> "
                + plan.product_changes["new_value"].astype(str),
            }
        ),
        pd.DataFrame(
            {
                "fault_type": "po_for_discontinued", "target": "purchase_orders",
                "date": discontinued_pos["order_date"], "store_id": discontinued_pos["store_id"],
                "item_id": discontinued_pos["item_id"],
                "detail": "po_id=" + discontinued_pos["po_id"],
            }
        ),
    ]
    if plan.schema_drift_date is not None:
        parts.append(
            pd.DataFrame(
                [{
                    "fault_type": "schema_drift", "target": "shipments",
                    "date": plan.schema_drift_date, "store_id": None, "item_id": None,
                    "detail": f"new column {plan.schema_drift_column}",
                }]
            )
        )
    gt = pd.concat([p for p in parts if len(p)], ignore_index=True)
    gt = gt.sort_values(["date", "fault_type"]).reset_index(drop=True)
    gt.insert(0, "fault_id", [f"F-{i:06d}" for i in range(1, len(gt) + 1)])
    return gt
