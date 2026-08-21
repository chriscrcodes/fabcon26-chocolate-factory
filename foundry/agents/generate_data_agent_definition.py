#!/usr/bin/env python3
"""Generate the `elements` tree for the shared Fabric Data Agent's two
datasource.json files (foundry/agents/data-agent/draft/*/datasource.json.tmpl).

Schema verified against
https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/data-agent-definition
(not guessed). Live-verified: creating the data agent with no `elements`
at all deploys successfully but leaves `"elements": []` -- a
datasource with zero tables selected, unusable for real queries. Every
table/column here must be listed explicitly and marked
`is_selected: true`; the API doesn't auto-discover a source's schema
from just an artifactId.

Column names/types are the real, already-verified schemas from
elsewhere in this repo, not guessed:
- Kusto columns match fabric/eventhouse/02_silver.kql's `.create table`
  declarations (Kusto native types: string/datetime/real).
- Lakehouse columns match the real Delta schema Spark's CSV
  auto-inference produced for the mirrored business tables (read live
  from each table's _delta_log during the Supply Chain/ERP ontology
  binding work -- see fabric/ontology/generate_fabric_iq_definition.py's
  LIVE_COLUMNS/docstring for the same values and the two surprises
  found there: customer.CreditLimit infers as an integer, and every
  *Date/LastUpdated column infers as a bare date, not a timestamp).

Second data source is a Lakehouse (`type: lakehouse_tables`), not the
Fabric SQL Database directly (`type: data_warehouse` was tried first
and live-verified NOT to work: the definition JSON is accepted without
error, the agent can even list the SQL Database's tables when asked to
introspect, but a real question against it fails -- "data_warehouse"
apparently doesn't function against a genuine `SQLDatabase`-type Fabric
item, only a true Warehouse item; there's no `sql_database` value in
the datasource `type` enum at all). Pointed at the same dimension
Lakehouse the Ontology already mirrors these 9 tables into
(fabric/ontology/deploy_dimension_lakehouse.py) instead -- reusing
existing infrastructure rather than debugging the SQL Database path
further. The Fabric SQL Database stays the actual system of record;
both the Ontology and this data agent read the same Lakehouse mirror.

Element `id` fields are deterministic (uuid5 from a namespace + name),
matching this repo's existing determinism convention
(fabric/ontology/generate_fabric_iq_definition.py's stable_id()).

Usage: uv run generate_data_agent_definition.py
Output: overwrites the `elements` field in-place in
foundry/agents/data-agent/draft/{kusto-eventhouse,lakehouse_tables-business_lakehouse}/datasource.json.tmpl
"""

import json
import uuid
from pathlib import Path

HERE = Path(__file__).parent
NAMESPACE = uuid.UUID("6f9c9a1a-2f2e-4b1a-9b3a-0f7b6e9d2c11")

KUSTO_TABLES = {
    "silver_batch": {
        "BatchId": "string",
        "LineId": "string",
        "RecipeId": "string",
        "StartTime": "datetime",
        "EndTime": "datetime",
    },
    "silver_quality_check": {
        "Timestamp": "datetime",
        "CheckId": "string",
        "BatchId": "string",
        "LineId": "string",
        "LineName": "string",
        "FactoryId": "string",
        "FactoryCode": "string",
        "StageId": "string",
        "StageName": "string",
        "Phase": "string",
        "DefectRate": "real",
        "Result": "string",
        "Notes": "string",
    },
    "silver_line_status": {
        "Timestamp": "datetime",
        "EventId": "string",
        "LineId": "string",
        "LineName": "string",
        "FactoryId": "string",
        "FactoryCode": "string",
        "Status": "string",
        "Reason": "string",
    },
}

# SQL-analytics-endpoint type names (nvarchar/float/date/int), not raw
# Delta types -- Lakehouse Tables are queried by the data agent through
# the Lakehouse's SQL analytics endpoint (T-SQL), same as Warehouse, so
# these should follow the same SQL-type convention the docs' own
# warehouse_tables.column example uses ("int"), not Spark/Delta's own
# type names ("string"/"double"/"date").
#
# UNRESOLVED as of this writing: neither this nor three other schema
# variants made the Lakehouse source actually queryable, despite each
# deploying without any schema/validation error. Tried, in order, all
# live-verified via the MCP endpoint (see foundry/agents/README.md for
# the exact test procedure): (1) type "lakehouse_tables" with a flat
# top-level table list, no wrapper -- same shape that works for Kusto;
# (2) same, wrapped in a "lakehouse_tables" root element (the enum's own
# "Lakehouse tables top level element") -- guessing a Lakehouse's two
# root branches (tables vs. files) need disambiguating, unlike Kusto's
# flat namespace; (3) top-level datasource `type: "lakehouse"` instead
# of `"lakehouse_tables"` -- rejected outright live ("Data source type
# is immutable" once created, forcing a revert, but also not the
# intended top-level value: LakehouseTables is what accepted "lakehouse_tables"
# renders as internally, so that value was correct); (4) this one,
# SQL-analytics type names in `elements` rather than Delta type names.
# In every case the agent can still describe the domain conceptually
# (from `userDescription`/`aiInstructions`) but reports the actual
# tables as inaccessible when asked a real question. Root cause is
# unconfirmed -- possibly a permissions/consent step only visible in
# the Fabric portal's own Data Agent UI (not exposed via REST), or a
# genuine current gap in Lakehouse Tables support for this shape.
# Eventhouse/Kusto binding works correctly and is verified end to end;
# this is the one open item from the agent-spine plan's step 1.
LAKEHOUSE_BUSINESS_TABLES = {
    "supplier": {"SupplierId": "nvarchar", "Name": "nvarchar", "Country": "nvarchar", "MaterialType": "nvarchar", "Rating": "float"},
    "material": {"MaterialId": "nvarchar", "SupplierId": "nvarchar", "Type": "nvarchar", "LotNumber": "nvarchar", "ReceivedDate": "date", "QuantityKg": "float"},
    "inventory": {"InventoryId": "nvarchar", "FactoryId": "nvarchar", "MaterialId": "nvarchar", "QuantityOnHand": "float", "ReorderLevel": "float", "LastUpdated": "date"},
    "shipment": {"ShipmentId": "nvarchar", "FromFactoryId": "nvarchar", "ToLocationId": "nvarchar", "BatchId": "nvarchar", "Carrier": "nvarchar", "DepartDate": "date", "ArriveDate": "date", "Status": "nvarchar"},
    "customer": {"CustomerId": "nvarchar", "Name": "nvarchar", "Country": "nvarchar", "Segment": "nvarchar", "CreditLimit": "int"},
    "product": {"ProductId": "nvarchar", "Name": "nvarchar", "RecipeId": "nvarchar", "PackagingType": "nvarchar", "SKU": "nvarchar"},
    "sales_order": {"OrderId": "nvarchar", "CustomerId": "nvarchar", "OrderDate": "date", "Status": "nvarchar", "TotalAmount": "float", "Currency": "nvarchar"},
    "order_line": {"OrderLineId": "nvarchar", "OrderId": "nvarchar", "ProductId": "nvarchar", "QuantityKg": "float", "UnitPrice": "float"},
    "invoice": {"InvoiceId": "nvarchar", "OrderId": "nvarchar", "IssueDate": "date", "DueDate": "date", "AmountDue": "float", "PaidDate": "date"},
}


def eid(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def kusto_elements() -> list[dict]:
    return [
        {
            "id": eid(f"kusto.table.{table}"),
            "is_selected": True,
            "display_name": table,
            "type": "kusto.table",
            "children": [
                {
                    "id": eid(f"kusto.column.{table}.{col}"),
                    "is_selected": True,
                    "display_name": col,
                    "type": "kusto.column",
                    "data_type": dtype,
                }
                for col, dtype in columns.items()
            ],
        }
        for table, columns in KUSTO_TABLES.items()
    ]


def lakehouse_elements() -> list[dict]:
    # Wrapped in a "lakehouse_tables" root element (the enum's own
    # "Lakehouse tables top level element" entry) -- unlike Kusto's flat
    # top-level table list (which worked live with no wrapper), a
    # Lakehouse has two root branches (lakehouse_tables vs
    # lakehouse_files), so the tables need this container to
    # disambiguate which branch they belong to.
    return [
        {
            "id": eid("lakehouse_tables.root"),
            "is_selected": True,
            "display_name": "Tables",
            "type": "lakehouse_tables",
            "children": [
                {
                    "id": eid(f"lakehouse_tables.table.{table}"),
                    "is_selected": True,
                    "display_name": table,
                    "type": "lakehouse_tables.table",
                    "children": [
                        {
                            "id": eid(f"lakehouse_tables.column.{table}.{col}"),
                            "is_selected": True,
                            "display_name": col,
                            "type": "lakehouse_tables.column",
                            "data_type": dtype,
                        }
                        for col, dtype in columns.items()
                    ],
                }
                for table, columns in LAKEHOUSE_BUSINESS_TABLES.items()
            ],
        }
    ]


def update_elements(path: Path, elements: list[dict]) -> None:
    obj = json.loads(path.read_text())
    obj["elements"] = elements
    path.write_text(json.dumps(obj, indent=2) + "\n")


def main() -> None:
    update_elements(HERE / "data-agent/draft/kusto-eventhouse/datasource.json.tmpl", kusto_elements())
    update_elements(
        HERE / "data-agent/draft/lakehouse_tables-business_lakehouse/datasource.json.tmpl", lakehouse_elements()
    )
    print(f"kusto: {len(KUSTO_TABLES)} tables")
    print(f"lakehouse: {len(LAKEHOUSE_BUSINESS_TABLES)} tables")


if __name__ == "__main__":
    main()
