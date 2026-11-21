"""Load M5 demand for the simulation window plus history for policy levels."""

from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ID_COLS = ["id", "item_id", "dept_id", "cat_id", "store_id", "state_id"]
LAST_SALES_DAY = 1941  # sales_train_evaluation.csv covers d_1 ... d_1941


@dataclass
class M5Data:
    demand: np.ndarray        # (T, N) daily units in the window
    hist_demand: np.ndarray   # (H, N) daily units BEFORE the window (for policy levels)
    meta: pd.DataFrame        # (N rows) id, item_id, dept_id, cat_id, store_id, state_id
    dates: pd.DatetimeIndex   # (T,) window dates
    avg_price: pd.Series      # item_id -> average sell price

    @property
    def hist_mean(self) -> np.ndarray:
        return self.hist_demand.mean(axis=0)

    @property
    def hist_std(self) -> np.ndarray:
        return self.hist_demand.std(axis=0)

    def head_days(self, days: int) -> "M5Data":
        """Return a copy limited to the first `days` days (for quick test runs)."""
        return M5Data(
            demand=self.demand[:days],
            hist_demand=self.hist_demand,
            meta=self.meta,
            dates=self.dates[:days],
            avg_price=self.avg_price,
        )


def load_m5(cfg: dict, root: Path) -> M5Data:
    raw = root / cfg["data"]["raw_dir"]

    cal = pd.read_csv(raw / "calendar.csv", usecols=["date", "d"], parse_dates=["date"])
    cal = cal[cal["d"].str[2:].astype(int) <= LAST_SALES_DAY]

    start = pd.Timestamp(cfg["window"]["start"])
    end = pd.Timestamp(cfg["window"]["end"])
    hist_start = start - pd.Timedelta(days=cfg["history_days"])

    win = cal[(cal["date"] >= start) & (cal["date"] <= end)]
    hist = cal[(cal["date"] >= hist_start) & (cal["date"] < start)]
    if win.empty:
        raise ValueError(f"No M5 days between {start.date()} and {end.date()}")
    if hist.empty:
        raise ValueError("No history before the window; reduce history_days or move start")

    hist_cols, win_cols = list(hist["d"]), list(win["d"])
    day_cols = hist_cols + win_cols
    sales = pd.read_csv(
        raw / "sales_train_evaluation.csv",
        usecols=ID_COLS + day_cols,
        dtype={c: "int16" for c in day_cols},
    )

    states = cfg["data"].get("states")
    if states:
        sales = sales[sales["state_id"].isin(states)].reset_index(drop=True)
    if sales.empty:
        raise ValueError(f"No series for states {states}")

    return M5Data(
        demand=sales[win_cols].to_numpy(dtype=np.int32).T,
        hist_demand=sales[hist_cols].to_numpy(dtype=np.int16).T,
        meta=sales[ID_COLS].copy(),
        dates=pd.DatetimeIndex(win["date"]),
        avg_price=_average_price(raw / "sell_prices.csv"),
    )


def _average_price(path: Path) -> pd.Series:
    df = duckdb.sql(
        f"SELECT item_id, AVG(sell_price) AS avg_price "
        f"FROM read_csv_auto('{path.as_posix()}') GROUP BY item_id"
    ).df()
    return df.set_index("item_id")["avg_price"]
