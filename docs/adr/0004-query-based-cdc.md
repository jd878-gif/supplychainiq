# ADR 0004: Query-based (watermark) CDC through Lakehouse Federation

- Status: Accepted
- Date: 2026-11-26

## Context
Products, suppliers and purchase orders live in an operational Postgres database (Azure Database for PostgreSQL,
Flexible Server). The lakehouse needs their changes for SCD2 product history and for the supplier scorecard.

## Decision
Read Postgres through a Unity Catalog **connection + foreign catalog** (Lakehouse Federation) and pull only rows with
`updated_at > watermark`. The watermark (`ops.cdc_watermarks`) moves only after the Bronze write succeeds, so delivery
is **at-least-once**; Silver MERGEs are keyed and idempotent, so a re-pull is harmless. Products become SCD2 in Silver.

## Alternatives considered
- **Log-based CDC (Debezium + Kafka, or a managed CDC service)**: captures every intermediate state and hard deletes,
  but needs logical replication settings, a connector runtime and a broker: more cost and moving parts than the
  student budget allows for one small database.
- **Full snapshot every run**: simple, but cost grows with table size and history must be rebuilt by diffing snapshots.

## Consequences
- Cheap and simple: no extra infrastructure, credentials stay in Unity Catalog, lineage is visible.
- Limits (accepted and documented): several changes between two pulls collapse into the last state; hard deletes are
  invisible (the source uses a `discontinued` flag instead); relies on the source always updating `updated_at`.
- Postgres must be running when the job runs; the server is stopped between runs to save credit.
- Reconciliation checks compare Silver counts with the source (1,059,859 purchase orders matched exactly).
