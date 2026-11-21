"""Check simulation outcomes against the validation criteria in config.yaml.

Criteria are relative (ADR 0003): absolute in-stock bands were replaced after
tuning showed in-stock rate is misleading for intermittent demand.
"""

import numpy as np
import pandas as pd

from supplychainiq.generator.load_m5 import M5Data
from supplychainiq.generator.simulate import SimResult


def tier_summary(result: SimResult, data: M5Data, cfg: dict) -> pd.DataFrame:
    """Demand, lost sales, fill rate and in-stock rate per supplier tier, after warm-up."""
    w = cfg["window"]["warmup_days"]
    on_hand_start = result.on_hand + result.sold  # stock available before sales
    tiers = result.series_supplier["reliability_tier"].to_numpy()

    rows = []
    for tier in ["reliable", "unreliable"]:
        cols = tiers == tier
        if not cols.any():
            continue
        demand = int(data.demand[w:, cols].sum())
        sold = int(result.sold[w:, cols].sum())
        rows.append(
            {
                "tier": tier,
                "series": int(cols.sum()),
                "demand_units": demand,
                "lost_units": demand - sold,
                "lost_sales_rate": round((demand - sold) / max(demand, 1), 4),
                "fill_rate": round(sold / max(demand, 1), 4),
                "in_stock_rate": round(float((on_hand_start[w:, cols] > 0).mean()), 4),
            }
        )
    return pd.DataFrame(rows)


def supplier_scorecard(result: SimResult, suppliers: pd.DataFrame) -> pd.DataFrame:
    """On-time and in-full rates per supplier, from received POs (ground truth)."""
    po = result.purchase_orders.merge(result.po_truth, on="po_id")
    po = po[po["arrived"]]
    card = (
        po.groupby("supplier_id")
        .agg(
            pos=("po_id", "count"),
            on_time_rate=("delay_days", lambda d: float(np.mean(d == 0))),
            avg_delay_days=("delay_days", "mean"),
            in_full_rate=("is_short", lambda s: float(1 - np.mean(s))),
        )
        .reset_index()
        .merge(suppliers[["supplier_id", "reliability_tier"]], on="supplier_id")
    )
    return card.sort_values("on_time_rate").round(3).reset_index(drop=True)


def check_criteria(summary: pd.DataFrame, card: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Evaluate the three relative criteria. Returns one row per criterion."""
    targets = cfg["validation_targets"]
    s = summary.set_index("tier")
    rows = []

    lo, hi = targets["reliable_in_stock"]
    rel_in_stock = float(s.loc["reliable", "in_stock_rate"])
    rows.append(
        {
            "criterion": "reliable in-stock rate",
            "value": f"{rel_in_stock:.1%}",
            "target": f"{lo:.0%}-{hi:.0%}",
            "passed": lo <= rel_in_stock <= hi,
        }
    )

    if "unreliable" in s.index:
        ratio = float(s.loc["unreliable", "lost_sales_rate"]) / max(
            float(s.loc["reliable", "lost_sales_rate"]), 1e-9
        )
        rows.append(
            {
                "criterion": "lost-sales ratio (unreliable / reliable)",
                "value": f"{ratio:.2f}x",
                "target": f">= {targets['min_lost_sales_ratio']:.1f}x",
                "passed": ratio >= targets["min_lost_sales_ratio"],
            }
        )

    if targets.get("unreliable_worst_in_scorecard") and card["reliability_tier"].nunique() == 2:
        worst_reliable = card.loc[card["reliability_tier"] == "reliable", "on_time_rate"].min()
        best_unreliable = card.loc[card["reliability_tier"] == "unreliable", "on_time_rate"].max()
        rows.append(
            {
                "criterion": "all unreliable suppliers rank worst (on-time)",
                "value": f"{best_unreliable:.3f} vs {worst_reliable:.3f}",
                "target": "best unreliable < worst reliable",
                "passed": bool(best_unreliable < worst_reliable),
            }
        )
    return pd.DataFrame(rows)
