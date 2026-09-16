"""Build graph-data.json for the static ontology viewer.

The instance graph (concrete factories/lines/suppliers/etc.) comes from
ontology_config.json plus the seeded CSVs in fabric/ontology/tables/ --
offline-derivable, no live connection needed.

The schema graph prefers deployed_schema.json -- a snapshot of the actually
deployed Fabric IQ Ontology item, fetched by fetch_deployed_schema.py -- so
entity/relationship counts and bound/unbound status match what's live
(e.g. sensor_reading is deployed as 6 per-stage entity types, not 1). If
deployed_schema.json is missing, it falls back to deriving the schema from
ontology_config.json, which is the pre-deployment abstraction and will
under-count relative to what's actually live.

Usage: python3 build_graph_data.py
"""

import csv
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "fabric" / "ontology" / "ontology_config.json"
TABLES_DIR = REPO_ROOT / "fabric" / "ontology" / "tables"
DEPLOYED_SCHEMA_PATH = Path(__file__).resolve().parent / "deployed_schema.json"
OUT_PATH = Path(__file__).resolve().parent / "graph-data.json"

DOMAINS = {
    "factory": ("Factory & Quality", "#A5522A", "\U0001f3ed"),
    "production_line": ("Factory & Quality", "#A5522A", "\U0001f3ed"),
    "production_stage": ("Factory & Quality", "#A5522A", "\U0001f3ed"),
    "recipe": ("Factory & Quality", "#A5522A", "\U0001f3ed"),
    "batch": ("Factory & Quality", "#A5522A", "\U0001f3ed"),
    "sensor_reading": ("Factory & Quality", "#A5522A", "\U0001f3ed"),
    "quality_check": ("Factory & Quality", "#A5522A", "\U0001f3ed"),
    "line_status": ("Factory & Quality", "#A5522A", "\U0001f3ed"),
    "supplier": ("Supply Chain", "#2F6E63", "\U0001f69a"),
    "material": ("Supply Chain", "#2F6E63", "\U0001f69a"),
    "inventory": ("Supply Chain", "#2F6E63", "\U0001f69a"),
    "batch_material_usage": ("Supply Chain", "#2F6E63", "\U0001f69a"),
    "shipment": ("Supply Chain", "#2F6E63", "\U0001f69a"),
    "customer": ("Orders", "#5B4B8A", "\U0001f9fe"),
    "product": ("Orders", "#5B4B8A", "\U0001f9fe"),
    "sales_order": ("Orders", "#5B4B8A", "\U0001f9fe"),
    "order_line": ("Orders", "#5B4B8A", "\U0001f9fe"),
    "invoice": ("Orders", "#5B4B8A", "\U0001f9fe"),
}

# Deployed entity display names (PascalCase) -> domain. sensor_reading is
# split into one entity per stage in the deployed definition; all six stay
# in Factory & Quality alongside their un-split source table's domain.
DOMAIN_BY_DEPLOYED_NAME = {
    "Factory": DOMAINS["factory"],
    "ProductionLine": DOMAINS["production_line"],
    "ProductionStage": DOMAINS["production_stage"],
    "Recipe": DOMAINS["recipe"],
    "Batch": DOMAINS["batch"],
    "QualityCheck": DOMAINS["quality_check"],
    "LineStatus": DOMAINS["line_status"],
    "Supplier": DOMAINS["supplier"],
    "Material": DOMAINS["material"],
    "Inventory": DOMAINS["inventory"],
    "BatchMaterialUsage": DOMAINS["batch_material_usage"],
    "Shipment": DOMAINS["shipment"],
    "Customer": DOMAINS["customer"],
    "Product": DOMAINS["product"],
    "SalesOrder": DOMAINS["sales_order"],
    "OrderLine": DOMAINS["order_line"],
    "Invoice": DOMAINS["invoice"],
    "SensorReadingGrinding": DOMAINS["sensor_reading"],
    "SensorReadingMixingRefining": DOMAINS["sensor_reading"],
    "SensorReadingConching": DOMAINS["sensor_reading"],
    "SensorReadingTempering": DOMAINS["sensor_reading"],
    "SensorReadingMoldingCooling": DOMAINS["sensor_reading"],
    "SensorReadingPackaging": DOMAINS["sensor_reading"],
}

# Deployed entity display name -> ontology_config.json table name, so the
# richer per-table detail (columns/types/key/notes) can be joined onto the
# deployed schema nodes. All six per-stage sensor entities share the one
# sensor_reading table definition.
DEPLOYED_TO_TABLE = {
    "Factory": "factory",
    "ProductionLine": "production_line",
    "ProductionStage": "production_stage",
    "Recipe": "recipe",
    "Batch": "batch",
    "QualityCheck": "quality_check",
    "LineStatus": "line_status",
    "Supplier": "supplier",
    "Material": "material",
    "Inventory": "inventory",
    "BatchMaterialUsage": "batch_material_usage",
    "Shipment": "shipment",
    "Customer": "customer",
    "Product": "product",
    "SalesOrder": "sales_order",
    "OrderLine": "order_line",
    "Invoice": "invoice",
    "SensorReadingGrinding": "sensor_reading",
    "SensorReadingMixingRefining": "sensor_reading",
    "SensorReadingConching": "sensor_reading",
    "SensorReadingTempering": "sensor_reading",
    "SensorReadingMoldingCooling": "sensor_reading",
    "SensorReadingPackaging": "sensor_reading",
}


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def build_schema_graph_from_config(config: dict) -> dict:
    """Fallback: derive the schema from ontology_config.json alone (no live
    connection). Under-counts relative to the deployed definition -- see
    module docstring."""
    nodes = [
        {
            "id": table_name,
            "label": table_name,
            "domain": DOMAINS[table_name][0],
            "color": DOMAINS[table_name][1],
            "icon": DOMAINS[table_name][2],
            "columns": table["columns"],
            "types": table["types"],
            "key": table["key"],
            "notes": table.get("notes", ""),
        }
        for table_name, table in config["tables"].items()
    ]
    edges = [
        {
            "id": rel["name"],
            "source": rel["from"],
            "target": rel["to"],
            "label": rel["name"],
            "bound": True,
            "fromKey": rel["fromKey"],
            "toKey": rel["toKey"],
        }
        for rel in config["relationships"]
    ]
    return {"nodes": nodes, "edges": edges}


def build_schema_graph_from_deployment(deployed: dict, config: dict) -> dict:
    tables = config["tables"]
    relationships_by_name = {rel["name"]: rel for rel in config["relationships"]}

    nodes = []
    for name in deployed["entities"]:
        domain, color, icon = DOMAIN_BY_DEPLOYED_NAME[name]
        table = tables[DEPLOYED_TO_TABLE[name]]
        nodes.append(
            {
                "id": name,
                "label": name,
                "domain": domain,
                "color": color,
                "icon": icon,
                "columns": table["columns"],
                "types": table["types"],
                "key": table["key"],
                "notes": table.get("notes", ""),
            }
        )

    edges = []
    for rel in deployed["relationships"]:
        config_rel = relationships_by_name.get(rel["name"], {})
        edges.append(
            {
                "id": rel["name"],
                "source": rel["source"],
                "target": rel["target"],
                "label": rel["name"],
                "bound": rel["bound"],
                "fromKey": config_rel.get("fromKey", ""),
                "toKey": config_rel.get("toKey", ""),
            }
        )
    return {"nodes": nodes, "edges": edges}


def load_table_rows(table_name: str) -> list[dict] | None:
    path = TABLES_DIR / f"{table_name}.csv"
    if not path.exists():
        return None
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_instance_graph(config: dict) -> dict:
    tables = config["tables"]
    rows_by_table = {name: load_table_rows(name) for name in tables}

    nodes = []
    for table_name, rows in rows_by_table.items():
        if rows is None:
            continue
        table = tables[table_name]
        key_col = table["key"]
        domain, color, icon = DOMAINS[table_name]
        for row in rows:
            nodes.append(
                {
                    "id": f"{table_name}:{row[key_col]}",
                    "label": row.get("Name") or row.get(key_col),
                    "table": table_name,
                    "domain": domain,
                    "color": color,
                    "icon": icon,
                    "properties": row,
                }
            )

    edges = []
    for rel in config["relationships"]:
        from_table, to_table = rel["from"], rel["to"]
        from_rows, to_rows = rows_by_table.get(from_table), rows_by_table.get(to_table)
        if from_rows is None or to_rows is None:
            continue

        to_key_col = tables[to_table]["key"]
        to_ids = {row[to_key_col] for row in to_rows}
        from_key_col = tables[from_table]["key"]

        for row in from_rows:
            fk_value = row.get(rel["fromKey"])
            if fk_value in to_ids:
                edges.append(
                    {
                        "id": f"{rel['name']}:{row[from_key_col]}",
                        "source": f"{from_table}:{row[from_key_col]}",
                        "target": f"{to_table}:{fk_value}",
                        "label": rel["name"],
                    }
                )

    return {"nodes": nodes, "edges": edges}


def build_map_data() -> list[dict]:
    """Factory rows joined with their production-line count, for the map tab.
    Coordinates are NOT included here -- they don't exist anywhere in the
    repo's data. index.html joins this against the hand-authored
    factory-coords.json at render time."""
    factories = load_table_rows("factory") or []
    lines = load_table_rows("production_line") or []
    line_count = {}
    for line in lines:
        line_count[line["FactoryId"]] = line_count.get(line["FactoryId"], 0) + 1

    return [
        {
            "FactoryId": f["FactoryId"],
            "Code": f["Code"],
            "City": f["City"],
            "Country": f["Country"],
            "Region": f["Region"],
            "TimeZone": f["TimeZone"],
            "LineCount": line_count.get(f["FactoryId"], 0),
        }
        for f in factories
    ]


def main() -> None:
    config = load_config()

    if DEPLOYED_SCHEMA_PATH.exists():
        deployed = json.loads(DEPLOYED_SCHEMA_PATH.read_text(encoding="utf-8"))
        schema = build_schema_graph_from_deployment(deployed, config)
        schema_source = "deployed Fabric IQ Ontology item (deployed_schema.json)"
    else:
        schema = build_schema_graph_from_config(config)
        schema_source = "ontology_config.json (no deployed_schema.json -- run fetch_deployed_schema.py for the live definition)"

    data = {
        "scenario": config["name"],
        "schema_source": schema_source,
        "schema": schema,
        "instances": build_instance_graph(config),
        "map": build_map_data(),
    }
    OUT_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"schema source: {schema_source}")
    print(
        f"wrote {OUT_PATH} -- schema: {len(data['schema']['nodes'])} entity types, "
        f"{len(data['schema']['edges'])} relationship types -- "
        f"instances: {len(data['instances']['nodes'])} nodes, "
        f"{len(data['instances']['edges'])} edges -- "
        f"map: {len(data['map'])} factories"
    )


if __name__ == "__main__":
    main()
