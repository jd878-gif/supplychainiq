"""Daily inventory simulation around real M5 demand.

The loop runs over DAYS (about 731), and every step updates ALL item-store
series at once with NumPy arrays. Looping over 22 million item-store-days in
Python would take hours; this takes seconds.

Each day, for every series:
  0. Refresh: every `recalc_every_days`, recompute s and S from the trailing
              `lookback_days` (past only, so no leakage).
  1. Receive: shipments arriving today are added to on hand; their POs close.
  2. Sell:    sold = min(demand, on hand); lost = demand - sold.
  3. Reorder: on the supplier's review weekday, if position <= s, order S - position.
              Actual arrival = today + promised lead time + sampled delay.
              Shipped qty may be short, depending on the supplier's tier.
  4. Record:  end-of-day on hand (the inventory snapshot).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from supplychainiq.generator.load_m5 import M5Data
from supplychainiq.generator.policy import policy_levels


@dataclass
class SimResult:
    on_hand: np.ndarray            # (T, N) end-of-day on hand
    sold: np.ndarray               # (T, N)
    lost: np.ndarray               # (T, N) unmet demand (stockout)
    received: np.ndarray           # (T, N) units received
    initial_on_hand: np.ndarray    # (N,)
    reorder_point: np.ndarray      # (N,)
    order_up_to: np.ndarray        # (N,)
    purchase_orders: pd.DataFrame  # public: what the ordering system knows
    shipments: pd.DataFrame        # public: what the warehouse records
    po_truth: pd.DataFrame         # ground truth: actual delay and short-ship per PO
    series_supplier: pd.DataFrame  # per series: supplier_id, tier, lead time


def trailing_stats(
    hist: np.ndarray, observed: np.ndarray, t: int, lookback: int
) -> tuple[np.ndarray, np.ndarray]:
    """Mean and std per series over the `lookback` days before day t.

    Uses only the past: the end of the pre-window history plus window days < t.
    """
    k_win = min(t, lookback)
    k_hist = lookback - k_win
    parts = []
    if k_hist > 0:
        parts.append(hist[-k_hist:])
    if k_win > 0:
        parts.append(observed[t - k_win : t])
    x = np.vstack(parts).astype(np.float64)
    return x.mean(axis=0), x.std(axis=0)


def _sample_delays(rng, dist: dict, n: int) -> np.ndarray:
    values = np.array([int(k) for k in dist], dtype=np.int64)
    probs = np.array(list(dist.values()), dtype=np.float64)
    return rng.choice(values, size=n, p=probs / probs.sum())


def simulate(
    data: M5Data,
    suppliers: pd.DataFrame,
    products: pd.DataFrame,
    cfg: dict,
    rng: np.random.Generator,
) -> SimResult:
    T, N = data.demand.shape

    # Per-series supplier attributes
    series = data.meta[["item_id", "store_id"]].merge(
        products[["item_id", "supplier_id"]], on="item_id", how="left"
    ).merge(suppliers, on="supplier_id", how="left")
    if series["supplier_id"].isna().any():
        raise ValueError("Some items have no supplier")
    lead = series["base_lead_time_days"].to_numpy(dtype=np.int64)
    review_wd = series["review_weekday"].to_numpy(dtype=np.int64)
    unreliable = (series["reliability_tier"] == "unreliable").to_numpy()

    pol = cfg["policy"]
    recalc_every = int(pol.get("recalc_every_days", 0) or 0)
    lookback = int(pol.get("lookback_days", data.hist_demand.shape[0]))
    use_sales = pol.get("signal", "demand") == "sales"

    def levels_at(t: int, observed: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        mean, std = trailing_stats(data.hist_demand, observed, t, lookback)
        return policy_levels(
            mean, std, lead,
            pol["review_period_days"], pol["service_level_z"], pol["min_order_up_to"],
        )

    s, S = levels_at(0, data.demand)

    max_delay = max(int(k) for d in cfg["delay_distribution"].values() for k in d)
    horizon = T + int(lead.max()) + max_delay + 1
    arriving_shipped = np.zeros((horizon, N), dtype=np.int32)
    arriving_ordered = np.zeros((horizon, N), dtype=np.int32)

    on_hand = S.astype(np.int32).copy()
    initial = on_hand.copy()
    on_order = np.zeros(N, dtype=np.int32)

    out_on_hand = np.zeros((T, N), dtype=np.int32)
    out_sold = np.zeros((T, N), dtype=np.int32)
    out_lost = np.zeros((T, N), dtype=np.int32)
    out_received = np.zeros((T, N), dtype=np.int32)

    weekdays = data.dates.day_of_week.to_numpy()
    po_parts = []
    fr = cfg["fill_rate"]
    dd = cfg["delay_distribution"]

    for t in range(T):
        # 0. Refresh policy levels from trailing (past-only) demand or sales
        if recalc_every and t > 0 and t % recalc_every == 0:
            s, S = levels_at(t, out_sold if use_sales else data.demand)

        # 1. Receive
        received = arriving_shipped[t]
        on_hand += received
        on_order -= arriving_ordered[t]
        out_received[t] = received

        # 2. Sell
        demand = data.demand[t]
        sold = np.minimum(demand, on_hand)
        on_hand -= sold
        out_sold[t] = sold
        out_lost[t] = demand - sold

        # 3. Reorder
        position = on_hand + on_order
        idx = np.nonzero((review_wd == weekdays[t]) & (position <= s))[0]
        if idx.size:
            qty = S[idx] - position[idx]
            tier_bad = unreliable[idx]

            delay = np.empty(idx.size, dtype=np.int64)
            delay[~tier_bad] = _sample_delays(rng, dd["reliable"], int((~tier_bad).sum()))
            delay[tier_bad] = _sample_delays(rng, dd["unreliable"], int(tier_bad.sum()))

            good, bad = fr["reliable"], fr["unreliable"]
            full_prob = np.where(tier_bad, bad["full_prob"], good["full_prob"])
            lo = np.where(tier_bad, bad["short_range"][0], good["short_range"][0])
            hi = np.where(tier_bad, bad["short_range"][1], good["short_range"][1])
            is_full = rng.random(idx.size) < full_prob
            short_qty = np.floor(qty * rng.uniform(lo, hi)).astype(np.int32)
            shipped = np.where(is_full, qty, short_qty).astype(np.int32)

            arrival = t + lead[idx] + delay
            np.add.at(arriving_shipped, (arrival, idx), shipped)
            np.add.at(arriving_ordered, (arrival, idx), qty)
            on_order[idx] += qty

            po_parts.append(
                pd.DataFrame(
                    {
                        "order_day": t, "series_idx": idx, "qty_ordered": qty,
                        "lead_time_days": lead[idx], "delay_days": delay,
                        "qty_shipped": shipped, "arrival_day": arrival,
                    }
                )
            )

        # 4. Record end-of-day stock
        out_on_hand[t] = on_hand

    purchase_orders, shipments, po_truth = _build_po_tables(
        po_parts, series, data.dates, cfg["transit_days"], T
    )
    return SimResult(
        on_hand=out_on_hand, sold=out_sold, lost=out_lost, received=out_received,
        initial_on_hand=initial, reorder_point=s, order_up_to=S,
        purchase_orders=purchase_orders, shipments=shipments, po_truth=po_truth,
        series_supplier=series[
            ["item_id", "store_id", "supplier_id", "reliability_tier", "base_lead_time_days"]
        ],
    )


def _build_po_tables(po_parts, series, dates, transit_days, T):
    cols_po = [
        "po_id", "store_id", "item_id", "supplier_id", "order_date",
        "expected_date", "qty_ordered", "status",
    ]
    cols_sh = ["shipment_id", "po_id", "ship_date", "arrival_date", "qty_shipped"]
    cols_truth = ["po_id", "delay_days", "qty_ordered", "qty_shipped", "is_short", "arrived"]
    if not po_parts:
        empty = pd.DataFrame
        return empty(columns=cols_po), empty(columns=cols_sh), empty(columns=cols_truth)

    po = pd.concat(po_parts, ignore_index=True)
    po["po_id"] = [f"PO-{i:08d}" for i in range(1, len(po) + 1)]
    po["store_id"] = series["store_id"].to_numpy()[po["series_idx"]]
    po["item_id"] = series["item_id"].to_numpy()[po["series_idx"]]
    po["supplier_id"] = series["supplier_id"].to_numpy()[po["series_idx"]]

    day0 = dates[0]
    po["order_date"] = day0 + pd.to_timedelta(po["order_day"], unit="D")
    po["expected_date"] = po["order_date"] + pd.to_timedelta(po["lead_time_days"], unit="D")
    po["arrival_date"] = day0 + pd.to_timedelta(po["arrival_day"], unit="D")
    po["arrived"] = po["arrival_day"] < T
    po["status"] = np.where(po["arrived"], "received", "open")
    po["is_short"] = po["qty_shipped"] < po["qty_ordered"]

    arrived = po[po["arrived"]].copy()
    arrived["shipment_id"] = [f"SH-{i:08d}" for i in range(1, len(arrived) + 1)]
    arrived["ship_date"] = arrived["arrival_date"] - pd.Timedelta(days=transit_days)

    return po[cols_po], arrived[cols_sh].reset_index(drop=True), po[cols_truth]
