#!/usr/bin/env python3
"""Sanity-check streamed telemetry after a backfill: row counts,
daily event density (should be roughly uniform across the backfilled
window -- a spike in any one day usually means an earlier partial
run's data wasn't cleared before re-backfilling, see
`clear_telemetry.py`), and a check for exact-duplicate ReadingIds.

Usage:
    KQL_QUERY_URI=<eventhouse query URI> KQL_DATABASE=<db name> \\
        uv run --with azure-kusto-data --with azure-identity \\
        verify_telemetry.py

Auth is `az login` (AzureCliCredential), same pattern as run_kql.py.
"""

import os
import time

from azure.kusto.data import KustoClient, KustoConnectionStringBuilder

TABLES = [
    "bronze_sensor_reading",
    "bronze_quality_check",
    "bronze_batch_event",
    "bronze_line_status",
]


def wait_for_ingestion(
    client: KustoClient, database: str, poll_seconds: int = 20, max_wait_seconds: int = 600
) -> None:
    """`bronze_sensor_reading`/`quality_check`/`line_status` have streaming
    ingestion disabled (see 01_bronze_and_reference.kql -- needed for their
    Silver update-policy joins), so they land via queued ingestion, which
    batches for up to a few minutes. Verifying immediately after a backfill
    finishes reads a table that's still filling in. Poll until the row
    count stops growing for two consecutive checks (or `max_wait_seconds`
    elapses) before reporting.
    """
    table = "bronze_sensor_reading"
    last_count = -1
    stable_checks = 0
    waited = 0
    while waited <= max_wait_seconds:
        result = client.execute_query(database, f"{table} | count")
        count = list(result.primary_results[0])[0]["Count"]
        if count == last_count:
            stable_checks += 1
            if stable_checks >= 2:
                return
        else:
            stable_checks = 0
        print(f"  waiting for ingestion to settle -- {table}: {count} row(s)")
        last_count = count
        time.sleep(poll_seconds)
        waited += poll_seconds
    print(f"  gave up waiting after {max_wait_seconds}s -- reporting current counts anyway")


def main() -> None:
    query_service_uri = os.environ["KQL_QUERY_URI"]
    database = os.environ["KQL_DATABASE"]

    kcsb = KustoConnectionStringBuilder.with_az_cli_authentication(query_service_uri)
    client = KustoClient(kcsb)

    print("waiting for queued ingestion to settle...")
    wait_for_ingestion(client, database)

    print("\nrow counts:")
    for table in TABLES:
        result = client.execute_query(database, f"{table} | count")
        row = list(result.primary_results[0])[0]
        print(f"  {table}: {row['Count']}")

    print("\ndaily event density (bronze_sensor_reading) -- flag any day that "
          "looks like an outlier vs. its neighbors:")
    q = """
    bronze_sensor_reading
    | summarize n=count() by day=bin(todatetime(Timestamp), 1d)
    | order by day asc
    """
    result = client.execute_query(database, q)
    for row in result.primary_results[0]:
        print(f"  {row['day']}: {row['n']}")

    print("\nexact-duplicate ReadingIds (should be 0):")
    q2 = """
    bronze_sensor_reading
    | summarize c=count() by ReadingId
    | where c > 1
    | count
    """
    result2 = client.execute_query(database, q2)
    dup_count = list(result2.primary_results[0])[0]["Count"]
    print(f"  {dup_count}")
    if dup_count:
        raise SystemExit(
            f"found {dup_count} duplicate ReadingId(s) -- clear and re-backfill "
            "(see clear_telemetry.py)"
        )


if __name__ == "__main__":
    main()
