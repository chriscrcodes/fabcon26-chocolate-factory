#!/usr/bin/env python3
"""Upload fabric/ontology/tables/*.csv into a Fabric Lakehouse and load
them as real Delta tables, so Fabric IQ Ontology can bind entities via
LakehouseTableDataBindingProperties. This covers two groups:

- Factory/Quality's dimension tables (factory, production_line,
  production_stage, recipe) -- Eventhouse bindings are TimeSeries-only
  (verified live), so these genuinely static tables can't be bound
  directly from the KQL database.
- Supply Chain/ERP's tables (supplier, material, inventory, shipment,
  customer, product, sales_order, order_line, invoice) -- the Ontology
  definition schema has exactly two sourceType options, LakehouseTable
  and KustoTable (Eventhouse, TimeSeries-only); there is no
  SqlDatabaseTable/WarehouseTable type (checked against
  https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/ontology-definition
  and https://learn.microsoft.com/en-us/fabric/iq/ontology/how-to-bind-data).
  So these tables -- whose CSVs already exist as the seed source for
  fabric/sql-database/deploy_sql_database.py -- are mirrored into this
  same Lakehouse too. The Fabric SQL Database stays the actual system
  of record the specialist agent queries; this Lakehouse copy exists
  solely to satisfy the Ontology's binding format requirement.

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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

HERE = Path(__file__).parent
TABLES_DIR = HERE / "tables"
# folder -> tables loaded into that Files/<folder>/ subfolder.
TABLE_GROUPS = {
    "dimensions": ["factory", "production_line", "production_stage", "recipe"],
    "business": [
        "supplier",
        "material",
        "inventory",
        "shipment",
        "customer",
        "product",
        "sales_order",
        "order_line",
        "invoice",
    ],
}
API_BASE = "https://api.fabric.microsoft.com/v1"


def upload_csv(file_system_client, lakehouse_id: str, folder: str, table: str) -> None:
    # OneLake paths accept either friendly names ("{name}.Lakehouse") or
    # GUIDs -- friendly-name resolution is disabled on this tenant
    # ("FriendlyNameSupportDisabled"), so use the lakehouse's item ID
    # directly.
    directory_client = file_system_client.get_directory_client(f"{lakehouse_id}/Files/{folder}")
    directory_client.create_directory()
    file_client = directory_client.get_file_client(f"{table}.csv")
    data = (TABLES_DIR / f"{table}.csv").read_bytes()
    file_client.upload_data(data, overwrite=True)


def load_table(session: requests.Session, workspace_id: str, lakehouse_id: str, folder: str, table: str) -> None:
    resp = session.post(
        f"{API_BASE}/workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables/{table}/load",
        json={
            "relativePath": f"Files/{folder}/{table}.csv",
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
    # The load-table polling loop below can run for minutes on a large
    # table; confirmed live that the connection gets dropped mid-poll
    # ("RemoteDisconnected") often enough on a fresh deploy to fail the
    # whole apply without this -- retries transient connection/5xx
    # errors with backoff instead of surfacing them as a hard failure.
    retry = Retry(total=5, backoff_factor=2, status_forcelist=[500, 502, 503, 504], allowed_methods=["GET", "POST"])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    token = credential.get_token("https://api.fabric.microsoft.com/.default").token
    session.headers["Authorization"] = f"Bearer {token}"

    for folder, tables in TABLE_GROUPS.items():
        for table in tables:
            upload_csv(file_system_client, lakehouse_id, folder, table)
            print(f"uploaded {table}.csv to Files/{folder}/")
            load_table(session, workspace_id, lakehouse_id, folder, table)
            print(f"loaded {table} as a Delta table")


if __name__ == "__main__":
    main()
