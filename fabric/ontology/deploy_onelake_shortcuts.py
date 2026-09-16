#!/usr/bin/env python3
"""Create OneLake shortcuts in the dimension Lakehouse pointing at
Eventhouse tables with OneLake availability enabled
(fabric/eventhouse/04_onelake_mirroring.kql), so those tables' data is
readable as ordinary Lakehouse Delta tables -- letting Fabric IQ
Ontology relationships whose "from" table is Eventhouse-bound get real
Contextualization instances despite Eventhouse tables never being a
valid Contextualization *source* directly (LakehouseTableDataBindingProperties
only). A shortcut is transparent to consumers: once created, it appears
in the Lakehouse's Tables/ area exactly like a native Delta table, same
`itemId`/`sourceTableName` binding shape already used for the mirrored
Supply Chain/ERP tables.

One REST call per table, `POST .../items/{lakehouseId}/shortcuts`:
https://learn.microsoft.com/en-us/rest/api/fabric/core/onelake-shortcuts/create-shortcut
Target is `oneLake` (same tenant), pointing at
`{kqlDatabaseItemId}/Tables/{table}` -- OneLake-availability-mirrored
data lands under the KQL *database* item's own OneLake namespace, not
the parent Eventhouse's (verified live via `.show table <T> policy
mirroring`'s ConnectionStrings value).

`silver_batch` is excluded -- it's a materialized view, and
`.alter-merge table silver_batch policy mirroring ...` fails live
("the requested endpoint ... does not exist"; the materialized-view
variant of the command doesn't parse at all). So `batch_to_line` and
`batch_to_recipe` stay type-only; converting the materialized view
into a form OneLake availability supports is a bigger follow-up, not
attempted here.

Auth: `az login` (AzureCliCredential), same pattern as the rest of
infra.
"""

import os
import time

import requests
from azure.identity import AzureCliCredential

API_BASE = "https://api.fabric.microsoft.com/v1"

# Mirroring can be accepted before OneLake exposes the table. Retry
# shortcut creation to absorb this short control-plane/data-plane delay.
RETRY_ATTEMPTS = 6
RETRY_DELAY_SECONDS = 15

# Eventhouse tables to expose via shortcut -- must already have OneLake
# availability enabled (fabric/eventhouse/04_onelake_mirroring.kql).
MIRRORED_TABLES = [
    "silver_quality_check",
    "silver_line_status",
    "silver_grinding",
    "silver_mixing_refining",
    "silver_conching",
    "silver_tempering",
    "silver_molding_cooling",
    "silver_packaging",
]


def main() -> None:
    workspace_id = os.environ["FABRIC_WORKSPACE_ID"]
    lakehouse_id = os.environ["FABRIC_LAKEHOUSE_ID"]
    kql_database_item_id = os.environ["FABRIC_KQL_DATABASE_ITEM_ID"]

    credential = AzureCliCredential()
    session = requests.Session()
    token = credential.get_token("https://api.fabric.microsoft.com/.default").token
    session.headers["Authorization"] = f"Bearer {token}"

    for table in MIRRORED_TABLES:
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            resp = session.post(
                f"{API_BASE}/workspaces/{workspace_id}/items/{lakehouse_id}/shortcuts",
                json={
                    "path": "Tables",
                    "name": table,
                    "target": {
                        "oneLake": {
                            "workspaceId": workspace_id,
                            "itemId": kql_database_item_id,
                            "path": f"Tables/{table}",
                        }
                    },
                },
            )
            if resp.status_code == 409:
                print(f"shortcut {table} already exists, skipping")
                break
            if resp.status_code == 400 and attempt < RETRY_ATTEMPTS:
                print(f"shortcut {table} got 400 (attempt {attempt}/{RETRY_ATTEMPTS}), retrying: {resp.text}")
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            resp.raise_for_status()
            print(f"created shortcut {table}")
            break


if __name__ == "__main__":
    main()
