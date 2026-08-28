#!/usr/bin/env python3
"""Clear the Fabric SQL Database's already-seeded Supply Chain/ERP
tables so `deploy_sql_database.py`'s seed step (skip-if-already-populated
-- see its `seed_table()`) actually reseeds them, instead of silently
keeping stale rows.

Why this exists: `deploy_sql_database.py` never re-inserts or updates
existing rows, only fills empty tables. `deploy_dimension_lakehouse.py`,
by contrast, always Load-Tables in `Overwrite` mode and is retriggered
on every `terraform apply` whose CSV content changed (file-hash
trigger). So whenever `fabric/ontology/tables/*.csv`'s content changes
(e.g. after adding a new generator function to
`simulator/src/business_data.py`, which reseeds the shared random
stream and shifts every downstream table's values -- confirmed live
when `batch_material_usage` was added), the dimension Lakehouse mirror
refreshes automatically but the SQL Database -- the actual system of
record -- does not, leaving the Ontology (reading the Lakehouse) and
the SQL Database silently inconsistent with each other. Run this once
before the next `terraform apply` whenever that happens.

Deletes in FK-safe order (children before parents). `supplier` and
`product` are never included here since neither has any randomness in
its generator after other tables' generators run -- their CSV content
never changes from a change elsewhere in business_data.py, so they
never go stale.

Usage:
    FABRIC_SQL_SERVER_FQDN=... FABRIC_SQL_DATABASE_NAME=... \\
        uv run --with azure-identity --with python-tds \\
        clear_business_tables.py

Auth is `az login` (AzureCliCredential), same pattern as
deploy_sql_database.py.
"""

import os

import certifi
import pytds
from azure.identity import AzureCliCredential

# Most-dependent first -- invoice/order_line/sales_order all trace back
# to customer; inventory/batch_material_usage trace back to material.
TABLES_TO_CLEAR = [
    "invoice",
    "order_line",
    "sales_order",
    "customer",
    "batch_material_usage",
    "inventory",
    "shipment",
    "material",
]


def main() -> None:
    # Same FQDN-parsing/connection setup as deploy_sql_database.py --
    # see there for why each of these settings is necessary.
    raw_server = os.environ["FABRIC_SQL_SERVER_FQDN"]
    server_fqdn, _, port_str = raw_server.partition(",")
    port = int(port_str) if port_str else 1433
    database_name = os.environ["FABRIC_SQL_DATABASE_NAME"]

    credential = AzureCliCredential()

    def get_token() -> str:
        return credential.get_token("https://database.windows.net/.default").token

    with pytds.connect(
        server=server_fqdn,
        database=database_name,
        port=port,
        access_token_callable=get_token,
        autocommit=True,
        cafile=os.environ.get("SSL_CERT_FILE") or certifi.where(),
        validate_host=False,
    ) as conn:
        with conn.cursor() as cursor:
            for table in TABLES_TO_CLEAR:
                cursor.execute(f"DELETE FROM {table}")
                print(f"cleared {table} ({cursor.rowcount} row(s) deleted)")


if __name__ == "__main__":
    main()
