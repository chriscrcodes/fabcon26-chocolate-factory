#!/usr/bin/env python3
"""Generate a Fabric IQ Ontology item definition (EntityTypes/DataBindings/
RelationshipTypes) from ontology_config.json, for the Factory/Quality
domain bound to the live Eventhouse + a small dimension Lakehouse.

Schema verified against
https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/ontology-definition
(not guessed).

Entity IDs and property IDs are deterministic (derived from a stable hash
of the name), so re-running this generator against an unchanged
ontology_config.json produces byte-identical output -- matching
generate_rdf.py's determinism.

7 of the 8 Factory/Quality entities are bound here. Two real constraints
found live shape which source each uses:

- `EventhouseTableDataBindingProperties` (`sourceType: KustoTable`) is
  **only accepted when `dataBindingType` is `TimeSeries`** -- a bare
  NonTimeSeries+KustoTable binding fails with an opaque
  `ALMOperationImportFailed` error (its message template is literally
  unfilled: "artifact '{1}' threw ... {2}"), only diagnosed by bisecting
  down to a single entity type with no binding at all (which succeeded,
  confirming the entity-type step was fine and the binding step was the
  failure), then to a direct REST call (bypassing Terraform) that
  returned the real error text Terraform was swallowing.
- For a TimeSeries entity, the identifying key column must be a
  **static** `properties` entry, not a `timeseriesProperties` one
  ("Entity keys cannot be specified when the entity type has no static
  properties. Entity keys can only reference static properties.").

So: `batch`, `quality_check`, `line_status` (genuinely time-indexed) bind
to the Eventhouse as TimeSeries; `factory`, `production_line`,
`production_stage`, `recipe` (genuine dimension data, no timestamp
column) bind to a small Lakehouse
(`demo/ontology/deploy_dimension_lakehouse.py` loads them from
demo/ontology/tables/*.csv as real Delta tables) as NonTimeSeries.

`sensor_reading` is still deferred -- EAV shaped, and its source table's
Timestamp column is string, not datetime (see
demo/eventhouse/README.md's "Verified against a live tenant" section).

Relationship *instances* (`RelationshipTypes/*/Contextualizations`) need
a `LakehouseTableDataBindingProperties` source regardless of which
entities they relate -- an Eventhouse table can't be a relationship
source at all. Only `line_to_factory` (production_line -> factory, both
Lakehouse-bound) gets an instance here, reusing production_line's own
table as the join source (it already carries both LineId and FactoryId
in one row, so no separate bridge table is needed). The other
relationships that touch an Eventhouse-bound entity
(batch_to_line/batch_to_recipe/check_to_line/check_to_stage/
line_status_to_line/check_to_batch) are declared as types only --
wiring their instances needs an Eventhouse -> Lakehouse export, which is
a bigger follow-up, not attempted here.

Usage: uv run generate_fabric_iq_definition.py
Output: demo/ontology/fabric_iq/ (definition.json, EntityTypes/, RelationshipTypes/)
"""

import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).parent
CONFIG = json.loads((HERE / "ontology_config.json").read_text())
OUT = HERE / "fabric_iq"

# Table name -> (source table, timestamp column). TimeSeries, bound to
# the Eventhouse via KustoTable.
EVENTHOUSE_BINDINGS = {
    "batch": ("silver_batch", "StartTime"),
    "quality_check": ("silver_quality_check", "Timestamp"),
    "line_status": ("silver_line_status", "Timestamp"),
}

# Table name -> source table (same name as the Delta table loaded by
# deploy_dimension_lakehouse.py). NonTimeSeries, bound via LakehouseTable.
LAKEHOUSE_BINDINGS = {
    "factory": "factory",
    "production_line": "production_line",
    "production_stage": "production_stage",
    "recipe": "recipe",
}

# Live columns per source table. Eventhouse ones verified via
# `<table> | getschema` against the deployed Eventhouse -- richer than
# ontology_config.json's abstract columns in places (e.g.
# silver_quality_check's LineName/FactoryCode enrichment), narrower in
# others (e.g. silver_batch has no QuantityKg/Status). Lakehouse ones
# match ontology_config.json exactly, since they're loaded verbatim from
# the same CSVs.
LIVE_COLUMNS = {
    "silver_batch": {"BatchId": "String", "LineId": "String", "RecipeId": "String", "StartTime": "DateTime", "EndTime": "DateTime"},
    "silver_quality_check": {"Timestamp": "DateTime", "CheckId": "String", "BatchId": "String", "LineId": "String", "LineName": "String", "FactoryId": "String", "FactoryCode": "String", "StageId": "String", "StageName": "String", "Phase": "String", "DefectRate": "Double", "Result": "String", "Notes": "String"},
    "silver_line_status": {"Timestamp": "DateTime", "EventId": "String", "LineId": "String", "LineName": "String", "FactoryId": "String", "FactoryCode": "String", "Status": "String", "Reason": "String"},
    "factory": {"FactoryId": "String", "Code": "String", "City": "String", "Country": "String", "Region": "String", "TimeZone": "String"},
    "production_line": {"LineId": "String", "FactoryId": "String", "LineNumber": "BigInt", "Name": "String"},
    "production_stage": {"StageId": "String", "Phase": "String", "Name": "String", "SequenceOrder": "BigInt"},
    "recipe": {"RecipeId": "String", "Name": "String", "CacaoPercent": "Double", "MilkPercent": "Double", "SugarPercent": "Double"},
}

# Preferred display-name column per table (falls back to the key column).
DISPLAY_NAME_COLUMN = {
    "factory": "Code",
    "production_line": "Name",
    "production_stage": "Name",
    "recipe": "Name",
}

FACTORY_QUALITY_TABLES = list(EVENTHOUSE_BINDINGS) + list(LAKEHOUSE_BINDINGS)
FACTORY_QUALITY_RELATIONSHIPS = [
    r
    for r in CONFIG["relationships"]
    if r["from"] in FACTORY_QUALITY_TABLES and r["to"] in FACTORY_QUALITY_TABLES
]

# Relationship instances (Contextualizations) -- see module docstring for
# why only this one is wired so far. dataBindingTable is the *table
# owning the FK column pair*, not necessarily either entity's own table
# in general, but here production_line's own table already carries both.
CONTEXTUALIZED_RELATIONSHIPS = {"line_to_factory"}


def stable_id(name: str) -> str:
    """Deterministic positive ~60-bit ID from a name -- fits Fabric IQ's
    BigInt ID requirement without a stored counter/registry."""
    return str(int(hashlib.sha256(name.encode()).hexdigest()[:15], 16))


def property_id(table: str, column: str) -> str:
    return stable_id(f"{table}.{column}")


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")


def as_property(table: str, col: str, value_type: str) -> dict:
    return {
        "id": property_id(table, col),
        "name": col,
        "redefines": None,
        "baseTypeNamespaceType": None,
        "valueType": value_type,
    }


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)

    write_json(OUT / "definition.json", {})

    entity_type_id = {table: stable_id(table) for table in FACTORY_QUALITY_TABLES}

    for table in FACTORY_QUALITY_TABLES:
        table_config = CONFIG["tables"][table]
        is_time_series = table in EVENTHOUSE_BINDINGS
        source_table = (
            EVENTHOUSE_BINDINGS[table][0] if is_time_series else LAKEHOUSE_BINDINGS[table]
        )
        columns = LIVE_COLUMNS[source_table]
        key_col = table_config["key"]

        eid = entity_type_id[table]
        entity_type = {
            "id": eid,
            "namespace": "usertypes",
            "baseEntityTypeId": None,
            "name": "".join(p.capitalize() for p in table.split("_")),
            "entityIdParts": [property_id(table, key_col)],
            "displayNamePropertyId": property_id(
                table, DISPLAY_NAME_COLUMN.get(table, key_col)
            ),
            "namespaceType": "Custom",
            "visibility": "Visible",
        }
        if is_time_series:
            # Verified live: "Entity keys cannot be specified when the
            # entity type has no static properties. Entity keys can only
            # reference static properties." -- the key column (constant
            # per entity instance) must be a static `properties` entry,
            # not a `timeseriesProperties` one, even though its data
            # still comes from the same time-indexed KQL table.
            entity_type["properties"] = [as_property(table, key_col, columns[key_col])]
            entity_type["timeseriesProperties"] = [
                as_property(table, col, value_type)
                for col, value_type in columns.items()
                if col != key_col
            ]
        else:
            entity_type["properties"] = [
                as_property(table, col, value_type) for col, value_type in columns.items()
            ]

        write_json(OUT / "EntityTypes" / eid / "definition.json.tmpl", entity_type)

        data_binding_id = stable_id(f"{table}.binding")
        if is_time_series:
            timestamp_col = EVENTHOUSE_BINDINGS[table][1]
            source_properties = {
                "sourceType": "KustoTable",
                "workspaceId": "{{ .WorkspaceId }}",
                "itemId": "{{ .EventhouseId }}",
                "clusterUri": "{{ .ClusterUri }}",
                "databaseName": "{{ .DatabaseName }}",
                "sourceTableName": source_table,
            }
            data_binding_config = {
                "dataBindingType": "TimeSeries",
                "timestampColumnName": timestamp_col,
                "propertyBindings": [
                    {"sourceColumnName": col, "targetPropertyId": property_id(table, col)}
                    for col in columns
                ],
                "sourceTableProperties": source_properties,
            }
        else:
            source_properties = {
                "sourceType": "LakehouseTable",
                "workspaceId": "{{ .WorkspaceId }}",
                "itemId": "{{ .LakehouseId }}",
                "sourceTableName": source_table,
            }
            data_binding_config = {
                "dataBindingType": "NonTimeSeries",
                "propertyBindings": [
                    {"sourceColumnName": col, "targetPropertyId": property_id(table, col)}
                    for col in columns
                ],
                "sourceTableProperties": source_properties,
            }

        data_binding = {"id": data_binding_id, "dataBindingConfiguration": data_binding_config}
        write_json(
            OUT / "EntityTypes" / eid / "DataBindings" / f"{data_binding_id}.json.tmpl",
            data_binding,
        )

    n_contextualizations = 0
    for rel in FACTORY_QUALITY_RELATIONSHIPS:
        rid = stable_id(rel["name"])
        relationship_type = {
            "namespace": "usertypes",
            "id": rid,
            "name": rel["name"],
            "namespaceType": "Custom",
            "source": {"entityTypeId": entity_type_id[rel["from"]]},
            "target": {"entityTypeId": entity_type_id[rel["to"]]},
        }
        write_json(OUT / "RelationshipTypes" / rid / "definition.json.tmpl", relationship_type)

        if rel["name"] in CONTEXTUALIZED_RELATIONSHIPS:
            from_table, to_table = rel["from"], rel["to"]
            contextualization_id = stable_id(f"{rel['name']}.contextualization")
            contextualization = {
                "id": contextualization_id,
                "dataBindingTable": {
                    "sourceType": "LakehouseTable",
                    "workspaceId": "{{ .WorkspaceId }}",
                    "itemId": "{{ .LakehouseId }}",
                    "sourceTableName": LAKEHOUSE_BINDINGS[from_table],
                },
                "sourceKeyRefBindings": [
                    {
                        "sourceColumnName": rel["fromKey"],
                        "targetPropertyId": property_id(from_table, CONFIG["tables"][from_table]["key"]),
                    }
                ],
                "targetKeyRefBindings": [
                    {
                        "sourceColumnName": rel["toKey"],
                        "targetPropertyId": property_id(to_table, CONFIG["tables"][to_table]["key"]),
                    }
                ],
            }
            write_json(
                OUT / "RelationshipTypes" / rid / "Contextualizations" / f"{contextualization_id}.json.tmpl",
                contextualization,
            )
            n_contextualizations += 1

    n_entities = len(FACTORY_QUALITY_TABLES)
    n_rels = len(FACTORY_QUALITY_RELATIONSHIPS)
    print(f"generated {n_entities} entity types + {n_rels} relationship types "
          f"({n_contextualizations} with instance data) -> {OUT}")
    print("(sensor_reading excluded -- EAV shape + string Timestamp, see module docstring)")


if __name__ == "__main__":
    main()
