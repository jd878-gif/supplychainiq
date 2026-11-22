"""Write the generated data as the source systems would deliver it.

Layout under <output_dir>:
  landing/pos/<arrival_date>/pos_<store>_<event_date>.jsonl[.gz]        POS events (JSON lines)
  landing/shipments/<date>/shipments_<date>.csv                       shipments that arrived
  landing/inventory/<date>/inventory_<store>_<date>.csv               end-of-day snapshots
  postgres_seed/{suppliers,products,product_changes,purchase_orders}.csv
  ground_truth/faults.csv, ground_truth/lost_sales.parquet

Folders are named by ARRIVAL date: a late POS file for 2015-03-10 that arrives
on 2015-03-12 sits in landing/pos/2015-03-12/. Its records still carry event_date.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from supplychainiq.generator.faults import FaultPlan
from supplychainiq.generator.load_m5 import M5Data
from supplychainiq.generator.simulate import SimResult


def write_landing_files(
    out: Path,
    data: M5Data,
    result: SimResult,
    plan: FaultPlan,
    cfg: dict,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Write POS, shipments and inventory files with faults applied.

    Returns the log of duplicated POS events (for the ground truth).
    """
    landing = out / "landing"
    store_arr = data.meta["store_id"].to_numpy()
    item_arr = data.meta["item_id"].to_numpy()
    price_arr = data.meta["item_id"].map(data.avg_price).fillna(1.0).round(2).to_numpy()
    stores = sorted(set(store_arr))
    store_cols = {s: np.nonzero(store_arr == s)[0] for s in stores}

    late = {
        (r.store_id, r.day_idx): r.arrival_date for r in plan.late_files.itertuples()
    }
    bad_by_day: dict[int, list] = {}
    for r in plan.bad_inventory.itertuples():
        bad_by_day.setdefault(r.day_idx, []).append((r.series_idx, r.bad_value))

    shipments = result.shipments.copy()
    shipments["arrival_date"] = pd.to_datetime(shipments["arrival_date"])
    ship_by_day = dict(tuple(shipments.groupby(shipments["arrival_date"])))
    # gzip by default: Auto Loader reads .gz natively and the upload is ~8x smaller
    gz = ".gz" if cfg["write"].get("compress", "gzip") == "gzip" else ""
    carriers = cfg["write"]["carriers"]

    carry: dict[str, pd.DataFrame] = {}   # duplicates re-sent in the next day's file
    dup_log = []

    for t, date in enumerate(data.dates):
        ds = date.strftime("%Y-%m-%d")

        # POS events
        sold_t = result.sold[t]
        for store in stores:
            cols = store_cols[store]
            idx = cols[sold_t[cols] > 0]
            ev = pd.DataFrame(
                {
                    "event_id": [f"{store}-{date:%Y%m%d}-{it}" for it in item_arr[idx]],
                    "event_ts": (
                        date + pd.to_timedelta(rng.integers(8 * 3600, 22 * 3600, len(idx)), "s")
                    ).strftime("%Y-%m-%dT%H:%M:%S"),
                    "event_date": ds,
                    "store_id": store,
                    "item_id": item_arr[idx],
                    "units": sold_t[idx],
                    "unit_price": price_arr[idx],
                }
            )
            n_dup = int(rng.binomial(len(ev), plan.duplicate_rate)) if len(ev) else 0
            dups = ev.sample(n=n_dup, random_state=int(rng.integers(1 << 31))) if n_dup else ev[:0]
            next_mask = rng.random(len(dups)) < plan.duplicate_next_file_share
            if t == len(data.dates) - 1:
                next_mask[:] = False  # no next file after the last day
            same_file, next_file = dups[~next_mask], dups[next_mask]

            file_df = pd.concat([ev, same_file, carry.pop(store, ev[:0])], ignore_index=True)
            if len(next_file):
                carry[store] = next_file
            for frame, file_day in ((same_file, date), (next_file, date + pd.Timedelta(days=1))):
                if len(frame):
                    dup_log.append(
                        frame[["event_id", "event_date", "store_id", "item_id"]].assign(
                            file_date=file_day
                        )
                    )

            arrival = late.get((store, t), date)
            folder = landing / "pos" / arrival.strftime("%Y-%m-%d")
            folder.mkdir(parents=True, exist_ok=True)
            file_df.to_json(folder / f"pos_{store}_{ds}.jsonl{gz}", orient="records", lines=True)

        # Shipments that arrived today
        sh = ship_by_day.get(date)
        if sh is not None and len(sh):
            sh = sh.copy()
            sh["ship_date"] = pd.to_datetime(sh["ship_date"]).dt.strftime("%Y-%m-%d")
            sh["arrival_date"] = ds
            if plan.schema_drift_date is not None and date >= plan.schema_drift_date:
                sh[plan.schema_drift_column] = rng.choice(carriers, len(sh))
            folder = landing / "shipments" / ds
            folder.mkdir(parents=True, exist_ok=True)
            sh.to_csv(folder / f"shipments_{ds}.csv{gz}", index=False)

        # Inventory snapshots (bad values injected)
        on_hand = result.on_hand[t].astype(np.int64)
        for series_idx, bad_value in bad_by_day.get(t, []):
            on_hand[series_idx] = bad_value
        folder = landing / "inventory" / ds
        folder.mkdir(parents=True, exist_ok=True)
        for store in stores:
            cols = store_cols[store]
            pd.DataFrame(
                {"snapshot_date": ds, "store_id": store, "item_id": item_arr[cols],
                 "on_hand": on_hand[cols]}
            ).to_csv(folder / f"inventory_{store}_{ds}.csv{gz}", index=False)

    if not dup_log:
        return pd.DataFrame(columns=["event_id", "event_date", "store_id", "item_id", "file_date"])
    log = pd.concat(dup_log, ignore_index=True)
    log["event_date"] = pd.to_datetime(log["event_date"])
    return log


def write_postgres_seed(
    out: Path,
    suppliers: pd.DataFrame,
    products: pd.DataFrame,
    purchase_orders: pd.DataFrame,
    shipments: pd.DataFrame,
    plan: FaultPlan,
    window_start: pd.Timestamp,
) -> None:
    """Initial table states + change logs, replayed into Postgres day by day later."""
    seed = out / "postgres_seed"
    seed.mkdir(parents=True, exist_ok=True)

    suppliers.assign(updated_at=window_start).to_csv(seed / "suppliers.csv", index=False)
    products.assign(status="active", updated_at=window_start).to_csv(
        seed / "products.csv", index=False
    )
    plan.product_changes.drop(columns=["day_idx"]).to_csv(
        seed / "product_changes.csv", index=False
    )

    # updated_at = when the row last changed: order date, or the day it was received
    po = purchase_orders.merge(shipments[["po_id", "arrival_date"]], on="po_id", how="left")
    po["created_at"] = po["order_date"]
    po["updated_at"] = po["arrival_date"].fillna(po["order_date"])
    po.drop(columns=["arrival_date"]).to_csv(seed / "purchase_orders.csv", index=False)


def write_ground_truth(out: Path, faults: pd.DataFrame, data: M5Data, result: SimResult) -> None:
    gt = out / "ground_truth"
    gt.mkdir(parents=True, exist_ok=True)
    faults.to_csv(gt / "faults.csv", index=False)

    t_idx, s_idx = np.nonzero(result.lost)
    pd.DataFrame(
        {
            "date": data.dates[t_idx],
            "store_id": data.meta["store_id"].to_numpy()[s_idx],
            "item_id": data.meta["item_id"].to_numpy()[s_idx],
            "demand": data.demand[t_idx, s_idx],
            "sold": result.sold[t_idx, s_idx],
            "lost": result.lost[t_idx, s_idx],
        }
    ).to_parquet(gt / "lost_sales.parquet", index=False)
