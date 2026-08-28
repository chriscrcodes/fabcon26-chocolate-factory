#!/usr/bin/env python3
"""Generate the `elements` tree for the shared Fabric Data Agent's
kusto-eventhouse/datasource.json (fabric/data-agent/draft/kusto-eventhouse/datasource.json.tmpl).

Schema verified against
https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/data-agent-definition
(not guessed). Live-verified: creating the data agent with no `elements`
at all deploys successfully but leaves `"elements": []` -- a
datasource with zero tables selected, unusable for real queries. Every
table/column here must be listed explicitly and marked
`is_selected: true`; the API doesn't auto-discover a source's schema
from just an artifactId.

Column types match fabric/eventhouse/02_silver.kql's `.create table`
declarations (Kusto native types: string/datetime/real).

`silver_batch` is deliberately excluded: it showed "This data source
has been deleted or you don't have permission to view it" in the
portal even though its `is_selected: true` entry is identical in shape
to the two tables that do work (silver_quality_check,
silver_line_status) -- consistent with it being a materialized view,
not a plain table (same category of thing that already blocked OneLake
mirroring for it elsewhere in this repo). batch_to_line/batch_to_recipe
already can't get Ontology relationship instances for the same reason.

This data agent is Eventhouse-only. It was originally also going to
cover Supply Chain/ERP via a second, Lakehouse-backed data source
(reusing the dimension Lakehouse's mirror of those 9 tables, built for
the Ontology), avoiding a separate data agent per domain. That path is
abandoned -- five configurations were tried live, including an exact
reproduction of a config built through the Fabric portal's own
"+ Add data source" picker (confirmed in the portal's own Sources view
as connected, no warning, all tables visible), and every one failed
identically both via this agent's MCP endpoint and the portal's own
native chat panel ("the available data sources do not contain supplier
information"). Concluded this is a current product limitation of Data
Agent + Lakehouse Tables for this data shape, not a configuration
mistake -- see SETUP.md's fabric/data-agent section for the full sequence tried
(kept there for anyone revisiting this once the underlying Fabric
feature matures). Supply Chain/ERP grounding for the Foundry agent
comes from the Fabric IQ Ontology (already covers all 9 tables with 27
real relationships) instead.

Element `id` fields are deterministic (uuid5 from a namespace + name),
matching this repo's existing determinism convention
(fabric/ontology/generate_fabric_iq_definition.py's stable_id()).

Usage: uv run generate_data_agent_definition.py
Output: overwrites the `elements` field in-place in
fabric/data-agent/draft/kusto-eventhouse/datasource.json.tmpl
"""

import json
import uuid
from pathlib import Path

HERE = Path(__file__).parent
NAMESPACE = uuid.UUID("6f9c9a1a-2f2e-4b1a-9b3a-0f7b6e9d2c11")

# silver_batch intentionally excluded -- see module docstring.
KUSTO_TABLES = {
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


def update_elements(path: Path, elements: list[dict]) -> None:
    obj = json.loads(path.read_text())
    obj["elements"] = elements
    path.write_text(json.dumps(obj, indent=2) + "\n")


def main() -> None:
    update_elements(HERE / "data-agent/draft/kusto-eventhouse/datasource.json.tmpl", kusto_elements())
    print(f"kusto: {len(KUSTO_TABLES)} tables")


if __name__ == "__main__":
    main()
