#!/usr/bin/env python3
"""Copy `batch`, `quality_check`, and `line_status` out of the Eventhouse
and into the dimension Lakehouse as real, native (non-shortcut) Delta
tables -- the missing static-binding source for those three Fabric IQ
Ontology entity types.

Why this exists: `generate_fabric_iq_definition.py` gives `batch`,
`quality_check`, `line_status`, and the 6 `sensor_reading_<stage>`
entities a TimeSeries binding straight to the Eventhouse, and correctly
carves each one's key column (`BatchId`/`CheckId`/`EventId`/`LineId`)
out into a static `properties` entry -- but never actually binds that
static property to a data source. Per Microsoft's own docs
(https://learn.microsoft.com/en-us/fabric/iq/ontology/how-to-bind-data):
"Before you bind time series data to an entity type, make sure your
static data binding is complete. The entity type must have at least one
property with static data bound to it." Confirmed live: the Fabric
portal's entity type details for `QualityCheck` shows "Missing static
binding," and the Ontology's GraphModel item fails to refresh at all
(`GraphNotRefreshable: "Graph doesn't have valid content"`) -- 9 of 22
entity types (these 3 plus all 6 per-stage sensor_reading entities)
were missing this required binding.

The static binding source must be **OneLake-backed and managed** (not
an external/shortcut table, per the same docs' limitations section) --
the existing OneLake-mirrored shortcuts
(`fabric/ontology/deploy_onelake_shortcuts.py`) don't qualify. The 6
`sensor_reading_<stage>` entities are keyed by `LineId` alone, which
already exists in the dimension Lakehouse's native `production_line`
table -- no new data needed for those, `generate_fabric_iq_definition.py`
just points their static binding there directly. `batch`/`quality_check`/
`line_status` are keyed by `BatchId`/`CheckId`/`EventId`, which exist
nowhere as a native Lakehouse table -- this script is what creates one.

Copies the full row (not just the key column) -- cheap at this data
volume (a few thousand to ~20k rows as of this writing) and leaves
headroom to bind more static properties from these tables later without
a second copy mechanism.

**Must be re-run before every graph refresh**, not just once: unlike
`deploy_dimension_lakehouse.py`'s CSV-sourced tables (loaded once,
static), these three keep growing as the simulator streams -- this
script always overwrites the full table rather than skipping if already
populated (contrast `run_kql.py`'s `ref_*` seeding, which intentionally
skips once populated because those really are static).

Auth: `az login` (AzureCliCredential), same pattern as the rest of
infra. Reuses the OneLake upload + Lakehouse "Load Table" mechanism
from deploy_dimension_lakehouse.py, and the Kusto query pattern from
../eventhouse/run_kql.py.

Usage:
    FABRIC_WORKSPACE_ID=... FABRIC_LAKEHOUSE_ID=... \\
    KQL_QUERY_URI=... KQL_DATABASE=... \\
        uv run --with azure-kusto-data --with azure-identity \\
        --with azure-storage-file-datalake --with requests \\
        materialize_static_sources.py
"""

import csv
import io
import os
import time

import requests
from azure.identity import AzureCliCredential
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.storage.filedatalake import DataLakeServiceClient
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_BASE = "https://api.fabric.microsoft.com/v1"
FOLDER = "ontology_static"

# Target Lakehouse table name -> source Eventhouse table.
SOURCE_TABLES = {
    "batch": "silver_batch",
    "quality_check": "silver_quality_check",
    "line_status": "silver_line_status",
}


def query_to_csv(kusto_client: KustoClient, database: str, source_table: str) -> bytes:
    result = kusto_client.execute_query(database, source_table)
    primary = result.primary_results[0]
    columns = [c.column_name for c in primary.columns]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in primary:
        # Kusto datetimes come back as datetime objects; everything else
        # (string/real/long) is already CSV-safe via str().
        writer.writerow([v.isoformat() if hasattr(v, "isoformat") else v for v in row])
    return buffer.getvalue().encode("utf-8")


def upload_csv(file_system_client, lakehouse_id: str, table: str, data: bytes) -> None:
    directory_client = file_system_client.get_directory_client(f"{lakehouse_id}/Files/{FOLDER}")
    directory_client.create_directory()
    file_client = directory_client.get_file_client(f"{table}.csv")
    file_client.upload_data(data, overwrite=True)


def load_table(session: requests.Session, workspace_id: str, lakehouse_id: str, table: str) -> None:
    resp = session.post(
        f"{API_BASE}/workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables/{table}/load",
        json={
            "relativePath": f"Files/{FOLDER}/{table}.csv",
            "pathType": "File",
            "mode": "Overwrite",
            "formatOptions": {"format": "Csv", "header": True, "delimiter": ","},
        },
    )
    resp.raise_for_status()
    if resp.status_code == 202:
        operation_url = resp.headers["Location"]
        while True:
            status_resp = session.get(operation_url)
            status_resp.raise_for_status()
            status = status_resp.json()
            if status["status"] == "Succeeded":
                break
            if status["status"] == "Failed":
                raise RuntimeError(f"Load table {table} failed: {status.get('error')}")
            time.sleep(int(status_resp.headers.get("Retry-After", 5)))


def main() -> None:
    workspace_id = os.environ["FABRIC_WORKSPACE_ID"]
    lakehouse_id = os.environ["FABRIC_LAKEHOUSE_ID"]
    query_service_uri = os.environ["KQL_QUERY_URI"]
    database = os.environ["KQL_DATABASE"]

    credential = AzureCliCredential()
    kcsb = KustoConnectionStringBuilder.with_az_cli_authentication(query_service_uri)
    kusto_client = KustoClient(kcsb)

    service_client = DataLakeServiceClient(
        account_url="https://onelake.dfs.fabric.microsoft.com", credential=credential
    )
    file_system_client = service_client.get_file_system_client(file_system=workspace_id)

    session = requests.Session()
    retry = Retry(total=5, backoff_factor=2, status_forcelist=[500, 502, 503, 504], allowed_methods=["GET", "POST"])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["Authorization"] = f"Bearer {credential.get_token('https://api.fabric.microsoft.com/.default').token}"

    for table, source_table in SOURCE_TABLES.items():
        data = query_to_csv(kusto_client, database, source_table)
        row_count = data.count(b"\n") - 1  # header line doesn't count
        upload_csv(file_system_client, lakehouse_id, table, data)
        print(f"uploaded {row_count} row(s) from {source_table} to Files/{FOLDER}/{table}.csv")
        load_table(session, workspace_id, lakehouse_id, table)
        print(f"loaded {table} as a native Delta table")


if __name__ == "__main__":
    main()
