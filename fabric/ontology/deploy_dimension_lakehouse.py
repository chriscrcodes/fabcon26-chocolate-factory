#!/usr/bin/env python3
"""Upload fabric/ontology/tables/*.csv into a Fabric Lakehouse and load
them as real Delta tables, so Fabric IQ Ontology can bind the
dimension entities (factory, production_line, production_stage, recipe)
via LakehouseTableDataBindingProperties -- Eventhouse bindings are
TimeSeries-only (verified live), so these genuinely static dimension
tables can't be bound directly from the KQL database.

Two steps per table, both documented Fabric/OneLake APIs (not guessed):
1. Upload the CSV to the Lakehouse's Files area over the OneLake DFS
   API (ADLS Gen2-compatible), via the azure-storage-file-datalake SDK.
   Path convention: https://onelake.dfs.fabric.microsoft.com/<workspace>/
   <lakehouse>.Lakehouse/Files/<path> -- see
   https://learn.microsoft.com/en-us/fabric/onelake/onelake-access-api
2. Call the Lakehouse "Load Table" API to materialize it as a Delta
   table (Overwrite mode) -- see
   https://learn.microsoft.com/en-us/rest/api/fabric/lakehouse/tables/load-table

Auth: `az login` (AzureCliCredential), same pattern as the rest of
infra.
"""

import os
import time
from pathlib import Path

import requests
from azure.identity import AzureCliCredential
from azure.storage.filedatalake import DataLakeServiceClient

HERE = Path(__file__).parent
TABLES_DIR = HERE / "tables"
DIMENSION_TABLES = ["factory", "production_line", "production_stage", "recipe"]
API_BASE = "https://api.fabric.microsoft.com/v1"


def upload_csv(file_system_client, lakehouse_id: str, table: str) -> None:
    # OneLake paths accept either friendly names ("{name}.Lakehouse") or
    # GUIDs -- friendly-name resolution is disabled on this tenant
    # ("FriendlyNameSupportDisabled"), so use the lakehouse's item ID
    # directly.
    directory_client = file_system_client.get_directory_client(f"{lakehouse_id}/Files/dimensions")
    directory_client.create_directory()
    file_client = directory_client.get_file_client(f"{table}.csv")
    data = (TABLES_DIR / f"{table}.csv").read_bytes()
    file_client.upload_data(data, overwrite=True)


def load_table(session: requests.Session, workspace_id: str, lakehouse_id: str, table: str) -> None:
    resp = session.post(
        f"{API_BASE}/workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables/{table}/load",
        json={
            "relativePath": f"Files/dimensions/{table}.csv",
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

    credential = AzureCliCredential()

    service_client = DataLakeServiceClient(
        account_url="https://onelake.dfs.fabric.microsoft.com", credential=credential
    )
    file_system_client = service_client.get_file_system_client(file_system=workspace_id)

    session = requests.Session()
    token = credential.get_token("https://api.fabric.microsoft.com/.default").token
    session.headers["Authorization"] = f"Bearer {token}"

    for table in DIMENSION_TABLES:
        upload_csv(file_system_client, lakehouse_id, table)
        print(f"uploaded {table}.csv to Files/dimensions/")
        load_table(session, workspace_id, lakehouse_id, table)
        print(f"loaded {table} as a Delta table")


if __name__ == "__main__":
    main()
