# ADR 0001: Build on Azure Databricks (serverless, Unity Catalog)

- Status: Accepted
- Date: 2026-11-26

## Context
The target roles (data engineer, analytics engineer, AI data engineer) increasingly list Databricks, Delta Lake,
Unity Catalog and lakehouse patterns. A portfolio project needs to show those on a real cloud workspace, not only locally.

Constraints (Azure for Students):
- about $100 of credit for the whole project;
- only one region is allowed by policy (Canada Central);
- VM quota: 4 DSv2 cores, 0 DDSv5 cores, and the one usable size (DS3_v2) was out of capacity.

## Decision
Azure Databricks, Premium workspace in Canada Central. ADLS Gen2 for storage, reached through an Access Connector
(managed identity) and Unity Catalog storage credentials and external locations. All compute is **serverless**
(notebooks, jobs, SQL warehouse); no classic clusters.

## Alternatives considered
- **Databricks on AWS / GCP**: same product, but no student credit available; Azure was the only free option.
- **Open-source Spark + Airflow + MinIO on a laptop or VMs**: free, but no Unity Catalog, no managed Auto Loader or
  serverless jobs, and it is not what the target postings ask for. Running and securing the stack would cost more time
  than the data work.
- **Classic clusters**: blocked in practice by the 4-core quota and the DS3_v2 stockout; also slower to start and
  easier to leave running by mistake.

## Consequences
- Easier: no cluster management, per-second billing, nothing to forget to turn off; Unity Catalog governs every
  table, volume and connection from day one.
- Harder: serverless hides Spark tuning (no cluster config, limited `spark.conf`); a session can be recycled, so
  notebooks must be re-runnable from the top; workspace-only sharing (dashboards are shown as screenshots).
- The data window is kept small (2 years, 3 states) so every run fits the credit budget.
