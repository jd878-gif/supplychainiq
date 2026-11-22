import gzip
import json

import numpy as np
import pandas as pd
import pytest
from sim_helpers import make_data, run

from supplychainiq.generator.config import DEFAULT_CONFIG, load_config
from supplychainiq.generator.faults import (
    apply_supplier_switches,
    ground_truth,
    plan_faults,
    pos_for_discontinued,
)
from supplychainiq.generator.writers import (
    write_ground_truth,
    write_landing_files,
    write_postgres_seed,
)


@pytest.fixture
def setup():
    cfg = load_config(DEFAULT_CONFIG)
    cfg["window"]["warmup_days"] = 5
    cfg["faults"]["late_files"]["count"] = 4
    cfg["faults"]["bad_inventory"]["count"] = 10
    cfg["faults"]["duplicate_pos_rate"] = 0.05
    cfg["faults"]["schema_drift"]["start_date"] = "2015-02-15"
    cfg["faults"]["product_updates"] = {
        "cost_changes": 6, "discontinued": 3, "supplier_switches": 4,
    }
    cfg["suppliers"]["per_department"] = 4
    cfg["suppliers"]["unreliable_share"] = 0.5
    data = make_data(n_items=60, days=90)
    suppliers, products, result = run(cfg, data)
    rng = np.random.default_rng(3)
    plan = plan_faults(data, result, products, suppliers, cfg, rng)
    return cfg, data, suppliers, products, result, plan, rng


def read_pos(out):
    files = sorted((out / "landing" / "pos").rglob("*.jsonl.gz"))
    frames = [pd.read_json(f, lines=True, dtype={"event_date": str}) for f in files]
    return files, pd.concat(frames, ignore_index=True)


def test_duplicates_match_log(tmp_path, setup):
    cfg, data, _, _, result, plan, rng = setup
    dups = write_landing_files(tmp_path, data, result, plan, cfg, rng)
    _, pos = read_pos(tmp_path)
    assert len(dups) > 0
    assert pos["event_id"].duplicated().sum() == len(dups)
    assert len(pos) - len(dups) == int((result.sold > 0).sum())
    assert (dups["file_date"] > dups["event_date"]).any()  # some re-sent in the next file


def test_late_files_land_in_arrival_folder(tmp_path, setup):
    cfg, data, _, _, result, plan, rng = setup
    write_landing_files(tmp_path, data, result, plan, cfg, rng)
    for r in plan.late_files.itertuples():
        ev = r.event_date.strftime("%Y-%m-%d")
        arr = r.arrival_date.strftime("%Y-%m-%d")
        assert (tmp_path / "landing" / "pos" / arr / f"pos_{r.store_id}_{ev}.jsonl.gz").exists()
        assert not (tmp_path / "landing" / "pos" / ev / f"pos_{r.store_id}_{ev}.jsonl.gz").exists()


def test_bad_inventory_written(tmp_path, setup):
    cfg, data, _, _, result, plan, rng = setup
    write_landing_files(tmp_path, data, result, plan, cfg, rng)
    for r in plan.bad_inventory.itertuples():
        d = r.date.strftime("%Y-%m-%d")
        path = tmp_path / "landing" / "inventory" / d / f"inventory_{r.store_id}_{d}.csv.gz"
        snap = pd.read_csv(path)
        assert snap.loc[snap["item_id"] == r.item_id, "on_hand"].iloc[0] == r.bad_value
    neg = plan.bad_inventory[plan.bad_inventory["kind"] == "negative_inventory"]
    assert (neg["bad_value"] < 0).all()


def test_schema_drift_only_from_start_date(tmp_path, setup):
    cfg, data, _, _, result, plan, rng = setup
    write_landing_files(tmp_path, data, result, plan, cfg, rng)
    for f in (tmp_path / "landing" / "shipments").rglob("*.csv.gz"):
        cols = pd.read_csv(f, nrows=1).columns
        day = pd.Timestamp(f.parent.name)
        assert ("carrier_code" in cols) == (day >= plan.schema_drift_date)


def test_schema_drift_outside_window_rejected(setup):
    cfg, data, suppliers, products, result, _, rng = setup
    cfg["faults"]["schema_drift"]["start_date"] = "2018-08-11"
    with pytest.raises(ValueError, match="outside the window"):
        plan_faults(data, result, products, suppliers, cfg, rng)


def test_supplier_switch_same_tier_and_relabels_later_pos(setup):
    _, _, suppliers, _, result, plan, _ = setup
    sw = plan.product_changes[plan.product_changes["change_type"] == "supplier_switch"]
    assert len(sw) > 0
    tier = suppliers.set_index("supplier_id")["reliability_tier"]
    assert (tier[sw["old_value"]].to_numpy() == tier[sw["new_value"]].to_numpy()).all()

    po = apply_supplier_switches(result.purchase_orders, plan.product_changes)
    for c in sw.itertuples():
        item_po = po[po["item_id"] == c.item_id]
        after = item_po["order_date"] >= c.effective_date
        assert (item_po.loc[after, "supplier_id"] == c.new_value).all()
        assert (item_po.loc[~after, "supplier_id"] == c.old_value).all()


def test_ground_truth_and_seed_files(tmp_path, setup):
    cfg, data, suppliers, products, result, plan, rng = setup
    po = apply_supplier_switches(result.purchase_orders, plan.product_changes)
    disc = pos_for_discontinued(po, plan.product_changes)
    dups = write_landing_files(tmp_path, data, result, plan, cfg, rng)
    write_postgres_seed(tmp_path, suppliers, products, po, result.shipments, plan, data.dates[0])
    gt = ground_truth(plan, dups, disc)
    write_ground_truth(tmp_path, gt, data, result)

    assert gt["fault_id"].is_unique
    counts = gt["fault_type"].value_counts()
    assert counts["duplicate_pos_event"] == len(dups)
    assert counts["late_file"] == 4
    assert counts["cost_change"] == 6 and counts["discontinued"] == 3
    assert counts["schema_drift"] == 1
    for name in ["suppliers", "products", "product_changes", "purchase_orders"]:
        assert (tmp_path / "postgres_seed" / f"{name}.csv").exists()
    lost = pd.read_parquet(tmp_path / "ground_truth" / "lost_sales.parquet")
    assert lost["lost"].sum() == result.lost.sum()


def test_pos_records_are_valid_json(tmp_path, setup):
    cfg, data, _, _, result, plan, rng = setup
    write_landing_files(tmp_path, data, result, plan, cfg, rng)
    files, _ = read_pos(tmp_path)
    with gzip.open(files[0], "rt") as fh:
        first = json.loads(fh.readline())
    assert set(first) == {"event_id", "event_ts", "event_date", "store_id", "item_id",
                          "units", "unit_price"}


def test_uncompressed_option(tmp_path, setup):
    cfg, data, _, _, result, plan, rng = setup
    cfg["write"]["compress"] = "none"
    write_landing_files(tmp_path, data, result, plan, cfg, rng)
    assert list((tmp_path / "landing" / "pos").rglob("*.jsonl"))
    assert not list((tmp_path / "landing").rglob("*.gz"))
