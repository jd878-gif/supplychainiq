"""Lakeflow Declarative Pipeline: shipments, product SCD2 and supplier KPIs, declared.

Same business logic as notebooks 11/22/31/40, rewritten declaratively:
- Auto Loader ingestion with automatic checkpoints, schema tracking and schema-evolution restarts
- data-quality rules as expectations (drop / warn); violations counted in the pipeline event log
- a quarantine table built from the same rules (bad rows kept, never silently lost)
- SCD Type 2 with AUTO CDC instead of a hand-written LEAD() window
- materialized views the engine can refresh incrementally
Writes to <catalog>.lakeflow; the notebook pipeline (bronze/silver/gold) is untouched.
"""

from pyspark import pipelines as dp
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.getActiveSession()
CAT = spark.conf.get("catalog")
LANDING = f"/Volumes/{CAT}/bronze/landing"

# one rule set, used twice: to drop bad rows from Silver and to route them to quarantine
SHIPMENT_RULES = {
    "no_rescued_data": "_rescued_data IS NULL",
    "keys_present": "shipment_id IS NOT NULL AND po_id IS NOT NULL",
    "qty_not_negative": "qty_shipped IS NOT NULL AND qty_shipped >= 0",
    "dates_valid": (
        "ship_date IS NOT NULL AND arrival_date IS NOT NULL AND arrival_date >= ship_date"
    ),
}
ALL_RULES = " AND ".join(f"({r})" for r in SHIPMENT_RULES.values())


# ---------------------------------------------------------------- Bronze
@dp.table(name="shipments_bronze", comment="Raw shipment files, as they arrived (Auto Loader).")
def shipments_bronze():
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "csv")
        .option("header", "true")
        .option("cloudFiles.inferColumnTypes", "true")
        .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
        .option("rescuedDataColumn", "_rescued_data")
        .load(f"{LANDING}/shipments/")
        .select(
            "*",
            F.col("_metadata.file_path").alias("_source_file"),
            F.current_timestamp().alias("_ingested_at"),
        )
    )


@dp.temporary_view(name="shipments_typed")
def shipments_typed():
    df = spark.readStream.table("shipments_bronze")
    # carrier_code only exists after the 2015-08-11 schema drift
    if "carrier_code" not in df.columns:
        df = df.withColumn("carrier_code", F.lit(None).cast("string"))
    return df.select(
        F.col("shipment_id").cast("string").alias("shipment_id"),
        F.col("po_id").cast("string").alias("po_id"),
        F.to_date("ship_date").alias("ship_date"),
        F.to_date("arrival_date").alias("arrival_date"),
        F.col("qty_shipped").cast("int").alias("qty_shipped"),
        F.col("carrier_code").cast("string").alias("carrier_code"),
        "_rescued_data",
        "_source_file",
        "_ingested_at",
    )


# ---------------------------------------------------------------- Silver + quarantine
@dp.table(
    name="shipments_silver",
    comment="Valid shipments. Zero-quantity shipments are valid and kept.",
    cluster_by=["arrival_date"],
)
@dp.expect_all_or_drop(SHIPMENT_RULES)
@dp.expect(
    "carrier_code_after_drift", "arrival_date < DATE'2015-08-11' OR carrier_code IS NOT NULL"
)
def shipments_silver():
    return spark.readStream.table("shipments_typed")


@dp.table(
    name="shipments_quarantine", comment="Shipments that failed at least one rule, with the reason."
)
def shipments_quarantine():
    reason = F.coalesce(
        *[F.when(~F.expr(rule), F.lit(name)) for name, rule in SHIPMENT_RULES.items()]
    )
    return (
        spark.readStream.table("shipments_typed")
        .where(f"NOT ({ALL_RULES})")  # rules are null-safe, so every row is either valid or here
        .withColumn("_dq_reason", reason)
    )


# ---------------------------------------------------------------- SCD2 with AUTO CDC
@dp.temporary_view(name="product_changes")
def product_changes():
    # bronze.products_cdc = watermark-CDC landing table (notebook 40); at-least-once: drop re-pulls
    return (
        spark.readStream.table(f"{CAT}.bronze.products_cdc")
        .select("item_id", "dept_id", "cat_id", "supplier_id", "unit_cost", "status", "updated_at")
        .dropDuplicates(["item_id", "updated_at"])
    )


dp.create_streaming_table(
    name="products_scd2",
    comment="Product history (SCD Type 2): __START_AT / __END_AT, current row has __END_AT NULL.",
)
dp.create_auto_cdc_flow(
    target="products_scd2",
    source="product_changes",
    keys=["item_id"],
    sequence_by=F.col("updated_at"),
    stored_as_scd_type=2,
)


# ---------------------------------------------------------------- Gold (materialized views)
@dp.materialized_view(
    name="supplier_delivery_monthly",
    comment="On-time, in-full and OTIF per supplier and month (received POs only).",
)
def supplier_delivery_monthly():
    s = spark.read.table("shipments_silver")
    p = spark.read.table(f"{CAT}.silver.purchase_orders").where("status = 'received'")
    d = (
        p.join(s, "po_id")
        .withColumn("on_time", (F.datediff("arrival_date", "expected_date") <= 0).cast("int"))
        .withColumn("in_full", (F.col("qty_shipped") >= F.col("qty_ordered")).cast("int"))
    )
    return d.groupBy(
        "supplier_id", F.date_trunc("month", "arrival_date").cast("date").alias("month")
    ).agg(
        F.count("*").alias("deliveries"),
        F.round(F.avg("on_time"), 3).alias("on_time_rate"),
        F.round(F.avg("in_full"), 3).alias("in_full_rate"),
        F.round(F.avg(F.col("on_time") * F.col("in_full")), 3).alias("otif_rate"),
        F.sum("qty_shipped").alias("units_shipped"),
    )


@dp.materialized_view(
    name="carrier_volume_daily",
    comment="Units and shipments per carrier per day (after the drift).",
)
def carrier_volume_daily():
    return (
        spark.read.table("shipments_silver")
        .where("carrier_code IS NOT NULL")
        .groupBy("arrival_date", "carrier_code")
        .agg(F.count("*").alias("shipments"), F.sum("qty_shipped").alias("units"))
    )
