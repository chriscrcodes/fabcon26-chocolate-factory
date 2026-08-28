#!/usr/bin/env python3
"""Clear all simulator-streamed telemetry from Bronze/Silver/Gold --
does NOT touch ref_* dimension tables (static, loaded once by
run_kql.py, not re-streamed). Run this before a fresh
`run_simulator.py --backfill-hours ...` to avoid double-counted,
overlapping data if a previous backfill only partially completed (see
`../../simulator/prepare_demo_data.sh`, which does exactly that).

Usage:
    KQL_QUERY_URI=<eventhouse query URI> KQL_DATABASE=<db name> \\
        uv run --with azure-kusto-data --with azure-identity \\
        clear_telemetry.py

Auth is `az login` (AzureCliCredential), same pattern as run_kql.py.

Table list is hand-kept in sync with 01_bronze_and_reference.kql/
02_silver.kql/03_gold.kql's `.create table`/`.create-or-alter
materialized-view` statements -- update here if those change. Silver
tables with OneLake mirroring enabled (see MIRRORED_TABLES below) have
that policy temporarily disabled and restored around the clear, since
Kusto refuses to drop extents a continuous export job is still
tracking.
"""

import os

from azure.kusto.data import KustoClient, KustoConnectionStringBuilder

REGULAR_TABLES = [
    "bronze_sensor_reading",
    "bronze_quality_check",
    "bronze_batch_event",
    "bronze_line_status",
    "silver_grinding",
    "silver_mixing_refining",
    "silver_conching",
    "silver_tempering",
    "silver_molding_cooling",
    "silver_packaging",
    "silver_quality_check",
    "silver_line_status",
]

MATERIALIZED_VIEWS = [
    "silver_batch",
    "gold_defect_rate_by_stage_daily",
]

# Every silver_* table (all except silver_quality_check/silver_line_status,
# which aren't mirrored) has OneLake mirroring enabled by
# 04_onelake_mirroring.kql for the Ontology binding -- `.clear table data`
# drops extents, which Kusto refuses while a continuous export job is still
# tracking them ("InvalidExtentsOperationException"). Disable mirroring
# around the clear, then restore the exact policy 04_onelake_mirroring.kql
# set (dataformat=parquet, TargetLatencyInMinutes=5).
MIRRORED_TABLES = [
    "silver_grinding",
    "silver_mixing_refining",
    "silver_conching",
    "silver_tempering",
    "silver_molding_cooling",
    "silver_packaging",
]


def main() -> None:
    query_service_uri = os.environ["KQL_QUERY_URI"]
    database = os.environ["KQL_DATABASE"]

    kcsb = KustoConnectionStringBuilder.with_az_cli_authentication(query_service_uri)
    client = KustoClient(kcsb)

    for table in MIRRORED_TABLES:
        print(f"disabling mirroring on {table} ...")
        client.execute_mgmt(
            database, f".alter-merge table {table} policy mirroring dataformat=parquet with (IsEnabled=false)"
        )

    for table in REGULAR_TABLES:
        print(f"clearing {table} ...")
        client.execute_mgmt(database, f".clear table {table} data")

    for view in MATERIALIZED_VIEWS:
        print(f"clearing materialized view {view} ...")
        client.execute_mgmt(database, f".clear materialized-view {view} data")

    for table in MIRRORED_TABLES:
        print(f"re-enabling mirroring on {table} ...")
        client.execute_mgmt(
            database,
            f".alter-merge table {table} policy mirroring dataformat=parquet "
            "with (IsEnabled=true, TargetLatencyInMinutes=5)",
        )

    print("done -- verifying row counts:")
    for table in REGULAR_TABLES + MATERIALIZED_VIEWS:
        result = client.execute_query(database, f"{table} | count")
        row = list(result.primary_results[0])[0]
        print(f"  {table}: {row['Count']}")


if __name__ == "__main__":
    main()
