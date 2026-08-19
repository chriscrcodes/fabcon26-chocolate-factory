#!/usr/bin/env python3
"""Deploy the Bronze/Silver/Gold KQL (01-03) and seed the ref_* dimension
tables against a live Fabric KQL database. Invoked by demo/infra's
null_resource.load_kql local-exec provisioner -- also runnable by hand
when iterating on the KQL directly:

    KQL_QUERY_URI=<eventhouse query URI> KQL_DATABASE=<db name> \\
        uv run --with azure-kusto-data --with azure-identity \\
        run_kql.py 01_bronze_and_reference.kql 02_silver.kql 03_gold.kql

Auth is `az login` (AzureCliCredential, via with_az_cli_authentication),
same pattern as the rest of demo/infra.

Statement boundary rule: a new statement starts at a line beginning with
"." while bracket/brace depth is 0 (tracked by net count of ( { vs ) }
per line). This matches how these scripts are structured -- multi-line
.create table (...)/.create-or-alter function ... { ... } blocks, and
single-line .alter statements back to back with no blank line needed.
Verified against a live tenant: exact statement counts for 01/02/03 are
11/26/4.
"""

import csv
import os
import sys
from pathlib import Path

from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.kusto.data.exceptions import KustoApiError, KustoServiceError

REF_TABLES = {
    "ref_factory": "factory.csv",
    "ref_production_line": "production_line.csv",
    "ref_production_stage": "production_stage.csv",
    "ref_recipe": "recipe.csv",
}


def strip_comments(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        if line.strip().startswith("//"):
            continue
        idx = line.find("//")
        if idx != -1:
            line = line[:idx]
        lines.append(line)
    return lines


def split_statements(lines: list[str]) -> list[str]:
    statements = []
    buffer: list[str] = []
    depth = 0
    for line in lines:
        if not line.strip():
            continue
        if line.lstrip().startswith(".") and depth == 0 and buffer:
            statements.append("\n".join(buffer))
            buffer = []
        buffer.append(line)
        depth += line.count("(") + line.count("{") - line.count(")") - line.count("}")
    if buffer:
        statements.append("\n".join(buffer))
    return statements


def run_kql_file(client: KustoClient, database: str, path: str) -> None:
    text = Path(path).read_text()
    statements = split_statements(strip_comments(text))
    print(f"=== {path}: {len(statements)} statement(s) ===")
    for i, stmt in enumerate(statements, 1):
        head = stmt.strip().splitlines()[0][:80]
        try:
            client.execute_mgmt(database, stmt)
            print(f"  [{i}/{len(statements)}] OK  {head}")
        except KustoApiError as e:
            # .create materialized-view isn't idempotent -- 02/03 use
            # .create-or-alter, but tolerate a pre-existing view either
            # way so a re-apply against an already-deployed database
            # doesn't fail the whole terraform apply.
            if "EntityAlreadyExistsException" in str(e):
                print(f"  [{i}/{len(statements)}] SKIP (already exists) {head}")
                continue
            print(f"  [{i}/{len(statements)}] FAIL {head}\n      {e}")
            raise
        except KustoServiceError as e:
            print(f"  [{i}/{len(statements)}] FAIL {head}\n      {e}")
            raise


def seed_reference_tables(client: KustoClient, database: str) -> None:
    tables_dir = Path(__file__).parent / ".." / "ontology" / "tables"
    for table, csv_name in REF_TABLES.items():
        count_result = client.execute_query(database, f"{table} | count")
        current_count = list(count_result.primary_results[0][0])[0]
        if current_count > 0:
            print(f"seed: {table} already has {current_count} row(s), skipping")
            continue

        csv_path = tables_dir / csv_name
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))[1:]  # drop header
        if not rows:
            continue
        body = "\n".join(",".join(row) for row in rows)
        client.execute_mgmt(database, f".ingest inline into table {table} <|\n{body}")
        print(f"seed: ingested {len(rows)} row(s) into {table}")


def main() -> None:
    query_service_uri = os.environ["KQL_QUERY_URI"]
    database = os.environ["KQL_DATABASE"]
    paths = sys.argv[1:]

    kcsb = KustoConnectionStringBuilder.with_az_cli_authentication(query_service_uri)
    client = KustoClient(kcsb)

    for path in paths:
        run_kql_file(client, database, path)

    seed_reference_tables(client, database)


if __name__ == "__main__":
    main()
