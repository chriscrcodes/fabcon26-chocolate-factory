#!/usr/bin/env python3
"""Generate the selected Eventhouse tables and columns for the shared
Fabric Data Agent.

The Data Agent requires an explicit `elements` list; it does not infer
schema from the artifactId alone. We intentionally keep this agent
Eventhouse-only; ERP/Supply Chain context is routed through the Fabric
IQ Ontology instead.

`silver_batch` is intentionally excluded because it behaves like a
materialized view and is not selectable like the working Silver tables.

Usage: uv run generate_data_agent_definition.py
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
