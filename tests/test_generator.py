import numpy as np
import pandas as pd
import pytest

from supplychainiq.generator.config import DEFAULT_CONFIG, load_config, validate_config
from supplychainiq.generator.load_m5 import M5Data, load_m5
from supplychainiq.generator.policy import policy_levels
from supplychainiq.generator.simulate import simulate, trailing_stats
from supplychainiq.generator.suppliers import build_suppliers
from supplychainiq.generator.validate import check_criteria, supplier_scorecard, tier_summary


@pytest.fixture
def cfg():
    return load_config(DEFAULT_CONFIG)


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


def test_policy_levels_order_up_to_above_reorder_point():
    s, S = policy_levels(np.array([2.0, 0.0]), np.array([1.0, 0.0]), np.array([3, 3]), 7, 1.65, 2)
    assert (S > s).all()
    assert S[1] == 2  # no history -> minimum stock
    # reorder point covers review period + lead time (7 + 3 = 10 days)
    assert s[0] == int(np.ceil(2 * 10 + 1.65 * 1 * np.sqrt(10)))
    assert S[0] == int(np.ceil(2 * 10 + 1.65 * np.sqrt(10) + 2 * 7))


def test_config_rejects_bad_probabilities(cfg):
    cfg["delay_distribution"]["reliable"] = {0: 0.5, 1: 0.2}
    with pytest.raises(ValueError):
        validate_config(cfg)


def test_sold_plus_lost_equals_demand(cfg):
    data = make_data()
    _, _, res = run(cfg, data)
    np.testing.assert_array_equal(res.sold + res.lost, data.demand)


def test_inventory_never_negative(cfg):
    _, _, res = run(cfg, make_data())
    assert (res.on_hand >= 0).all()


def test_inventory_balance(cfg):
    data = make_data()
    _, _, res = run(cfg, data)
    expected_end = res.initial_on_hand + res.received.sum(axis=0) - res.sold.sum(axis=0)
    np.testing.assert_array_equal(res.on_hand[-1], expected_end)


def test_shipments_match_received_units(cfg):
    data = make_data()
    _, _, res = run(cfg, data)
    assert res.shipments["qty_shipped"].sum() == res.received.sum()


def test_every_item_has_one_supplier_in_its_department(cfg):
    data = make_data()
    suppliers, products, _ = run(cfg, data)
    assert products["item_id"].is_unique
    merged = products.merge(suppliers, on="supplier_id", suffixes=("", "_sup"))
    assert (merged["dept_id"] == merged["dept_id_sup"]).all()


def test_unreliable_suppliers_are_worse(cfg):
    cfg["suppliers"]["unreliable_share"] = 0.5
    data = make_data(n_items=90, days=200)
    suppliers, _, res = run(cfg, data)
    summary = tier_summary(res, data, cfg).set_index("tier")
    assert summary.loc["unreliable", "fill_rate"] < summary.loc["reliable", "fill_rate"]
    card = supplier_scorecard(res, suppliers)
    worst = card.head(2)["reliability_tier"]
    assert (worst == "unreliable").all()


def test_same_seed_same_result(cfg):
    data = make_data()
    _, _, a = run(cfg, data, seed=7)
    _, _, b = run(cfg, data, seed=7)
    np.testing.assert_array_equal(a.on_hand, b.on_hand)
    assert a.purchase_orders.equals(b.purchase_orders)


def test_load_m5_from_csv_files(tmp_path, cfg):
    raw = tmp_path / "data" / "raw" / "m5"
    raw.mkdir(parents=True)
    dates = pd.date_range("2014-01-01", periods=60, freq="D")
    calendar = pd.DataFrame(
        {"date": dates.strftime("%Y-%m-%d"), "d": [f"d_{i}" for i in range(1, 61)]}
    )
    calendar.to_csv(raw / "calendar.csv", index=False)
    sales = pd.DataFrame(
        {"id": ["A_CA_1", "A_TX_1"], "item_id": ["FOODS_1_001"] * 2, "dept_id": ["FOODS_1"] * 2,
         "cat_id": ["FOODS"] * 2, "store_id": ["CA_1", "TX_1"], "state_id": ["CA", "TX"]}
    )
    for i in range(1, 61):
        sales[f"d_{i}"] = i % 3
    sales.to_csv(raw / "sales_train_evaluation.csv", index=False)
    pd.DataFrame(
        {"store_id": ["CA_1"], "item_id": ["FOODS_1_001"], "wm_yr_wk": [1], "sell_price": [2.5]}
    ).to_csv(raw / "sell_prices.csv", index=False)

    cfg["window"] = {"start": "2014-02-01", "end": "2014-03-01", "warmup_days": 0}
    cfg["history_days"] = 31
    cfg["data"]["states"] = ["CA"]
    data = load_m5(cfg, tmp_path)
    assert data.demand.shape == (29, 1)
    assert data.dates[0] == pd.Timestamp("2014-02-01")
    assert data.avg_price["FOODS_1_001"] == 2.5


def test_trailing_stats_uses_only_the_past():
    hist = np.full((10, 1), 1, dtype=np.int16)
    observed = np.array([[5], [5], [100]], dtype=np.int32)  # day 2 is the "future"
    mean, _ = trailing_stats(hist, observed, t=2, lookback=4)
    # 2 history days (1, 1) + window days 0-1 (5, 5); day 2 must not be used
    assert mean[0] == 3.0


def test_recalculation_adapts_to_demand_growth(cfg):
    data = make_data(n_items=30, days=240)
    data.demand[120:] *= 3  # demand triples halfway through
    cfg["window"]["warmup_days"] = 0

    cfg["policy"]["recalc_every_days"] = 0  # static levels
    _, _, static = run(cfg, data)
    cfg["policy"]["recalc_every_days"] = 28  # monthly refresh
    _, _, rolling = run(cfg, data)

    assert rolling.lost[120:].sum() < static.lost[120:].sum() * 0.5


def test_check_criteria_relative_targets(cfg):
    summary = pd.DataFrame(
        [
            {"tier": "reliable", "lost_sales_rate": 0.04, "in_stock_rate": 0.97},
            {"tier": "unreliable", "lost_sales_rate": 0.10, "in_stock_rate": 0.94},
        ]
    )
    card = pd.DataFrame(
        {"on_time_rate": [0.45, 0.46, 0.91, 0.92],
         "reliability_tier": ["unreliable", "unreliable", "reliable", "reliable"]}
    )
    result = check_criteria(summary, card, cfg)
    assert result["passed"].all()

    summary.loc[1, "lost_sales_rate"] = 0.05  # only 1.25x worse
    assert not check_criteria(summary, card, cfg)["passed"].all()
