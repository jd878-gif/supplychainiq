"""Replay the generator's operational data into Postgres up to a date.

This simulates the source system (an ERP / procurement database) moving through time,
the same way upload_landing.ps1 simulates files arriving.

First run: creates the tables and loads suppliers + products as of the window start.
Every run then applies, for business days after the last replayed date up to --until:
  * product changes (cost change, supplier switch, discontinued) -> UPDATE products
  * new purchase orders (order_date in range)                      -> INSERT status 'open'
  * PO receipts (received date in range)                           -> UPDATE status 'received'
Every changed row gets updated_at = its business date. That column drives query-based CDC.
The whole run is one transaction: it either fully applies or changes nothing.

Usage (venv active, from the repo root):
  PowerShell:  $env:PGPASSWORD = "<password>"
  Git Bash:    export PGPASSWORD='<password>'
  python scripts/pg_replay.py --until 2015-08-20
"""

import argparse
import io
from pathlib import Path

import pandas as pd
import psycopg

DDL = """
-- reliability_tier is deliberately NOT stored: it is ground truth. The scorecard must
-- discover unreliable suppliers from delivery data, like a real buyer would.
CREATE TABLE IF NOT EXISTS suppliers (
  supplier_id          text PRIMARY KEY,
  dept_id              text,
  cat_id               text,
  base_lead_time_days  int,
  review_weekday       int,
  updated_at           timestamp NOT NULL);
CREATE TABLE IF NOT EXISTS products (
  item_id      text PRIMARY KEY,
  dept_id      text,
  cat_id       text,
  supplier_id  text REFERENCES suppliers,
  unit_cost    numeric(10,2),
  status       text,
  updated_at   timestamp NOT NULL);
CREATE TABLE IF NOT EXISTS purchase_orders (
  po_id          text PRIMARY KEY,
  store_id       text,
  item_id        text REFERENCES products,
  supplier_id    text REFERENCES suppliers,
  order_date     date,
  expected_date  date,
  qty_ordered    int,
  status         text,
  created_at     timestamp NOT NULL,
  updated_at     timestamp NOT NULL);
CREATE INDEX IF NOT EXISTS ix_suppliers_updated ON suppliers (updated_at);
CREATE INDEX IF NOT EXISTS ix_products_updated  ON products (updated_at);
CREATE INDEX IF NOT EXISTS ix_po_updated        ON purchase_orders (updated_at);
CREATE TABLE IF NOT EXISTS replay_state (
  id              int PRIMARY KEY CHECK (id = 1),
  replayed_until  date NOT NULL);
"""

PO_COLS = ["po_id", "store_id", "item_id", "supplier_id", "order_date", "expected_date",
           "qty_ordered", "status", "created_at", "updated_at"]
CHANGE_FIELDS = {"unit_cost": float, "supplier_id": str, "status": str}


def copy_df(cur: psycopg.Cursor, table: str, df: pd.DataFrame) -> None:
    """Bulk load with COPY (much faster than INSERTs)."""
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False)
    buf.seek(0)
    with cur.copy(f"COPY {table} ({', '.join(df.columns)}) FROM STDIN WITH (FORMAT csv)") as cp:
        while chunk := buf.read(1 << 20):
            cp.write(chunk)


def day_before(date: str) -> str:
    return (pd.Timestamp(date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def replay(conn: psycopg.Connection, seed: Path, until: str) -> dict:
    stats = {}
    with conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute("SELECT replayed_until FROM replay_state WHERE id = 1")
        row = cur.fetchone()

        if row is None:  # first run: initial master data
            sup = pd.read_csv(seed / "suppliers.csv", dtype=str).drop(columns=["reliability_tier"])
            prod = pd.read_csv(seed / "products.csv", dtype=str)
            copy_df(cur, "suppliers", sup)
            copy_df(cur, "products", prod)
            last = day_before(prod["updated_at"].min())
            cur.execute("INSERT INTO replay_state VALUES (1, %s)", (last,))
            stats.update(suppliers_loaded=len(sup), products_loaded=len(prod))
        else:
            last = row[0].strftime("%Y-%m-%d")

        if until < last:
            raise SystemExit(f"--until {until} is before the last replayed date {last}; "
                             "the source can only move forward.")
        if until == last:
            stats["note"] = f"already replayed up to {until}; nothing to do"
            return stats

        # 1) product changes, in business order
        changes = pd.read_csv(seed / "product_changes.csv", dtype=str)
        changes = changes[(changes["effective_date"] > last) & (changes["effective_date"] <= until)]
        for c in changes.sort_values(["effective_date", "change_id"]).itertuples():
            if c.field not in CHANGE_FIELDS:
                raise ValueError(f"unexpected change field {c.field!r}")
            value = CHANGE_FIELDS[c.field](c.new_value)
            cur.execute(f"UPDATE products SET {c.field} = %s, updated_at = %s WHERE item_id = %s",
                        (value, c.effective_date, c.item_id))
        stats["product_changes_applied"] = len(changes)

        # 2) new purchase orders arrive as 'open'
        po = pd.read_csv(seed / "purchase_orders.csv", dtype=str)
        new = po[(po["order_date"] > last) & (po["order_date"] <= until)]
        copy_df(cur, "purchase_orders",
                new.assign(status="open", updated_at=new["created_at"])[PO_COLS])
        stats["pos_inserted"] = len(new)

        # 3) receipts flip POs to 'received' on the day the shipment arrived
        recv = po[(po["status"] == "received") & (po["updated_at"] > last)
                  & (po["updated_at"] <= until)][["po_id", "updated_at"]]
        cur.execute("CREATE TEMP TABLE _recv (po_id text, updated_at timestamp) ON COMMIT DROP")
        copy_df(cur, "_recv", recv)
        cur.execute("""UPDATE purchase_orders p
                       SET status = 'received', updated_at = r.updated_at
                       FROM _recv r WHERE p.po_id = r.po_id""")
        stats["pos_received"] = cur.rowcount

        cur.execute("UPDATE replay_state SET replayed_until = %s WHERE id = 1", (until,))
        stats["replayed"] = f"{last} -> {until}"
    return stats


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--until", required=True, help="replay up to this date (YYYY-MM-DD)")
    p.add_argument("--seed-dir", default="data/generated/postgres_seed")
    p.add_argument("--host", default="pg-supplychainiq-jd.postgres.database.azure.com")
    p.add_argument("--port", type=int, default=5432)
    p.add_argument("--user", default="sciqadmin")
    p.add_argument("--db", default="supplychain")
    p.add_argument("--sslmode", default="require")
    args = p.parse_args()
    until = pd.Timestamp(args.until).strftime("%Y-%m-%d")

    # password comes from the PGPASSWORD environment variable (never from code or args)
    with psycopg.connect(host=args.host, port=args.port, user=args.user, dbname=args.db,
                         sslmode=args.sslmode, connect_timeout=15) as conn:
        stats = replay(conn, Path(args.seed_dir), until)   # commits on clean exit
        with conn.cursor() as cur:
            for t in ["suppliers", "products", "purchase_orders"]:
                cur.execute(f"SELECT count(*), max(updated_at) FROM {t}")
                n, mx = cur.fetchone()
                stats[f"{t}_rows"] = f"{n:,} (max updated_at {mx})"
    for k, v in stats.items():
        print(f"{k:>24}: {v}")


if __name__ == "__main__":
    main()
