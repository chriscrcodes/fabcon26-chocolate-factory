#!/usr/bin/env python3
"""Generate a Fabric IQ Ontology item definition (EntityTypes/DataBindings/
RelationshipTypes) from ontology_config.json, for the Factory/Quality
domain bound to the live Eventhouse.

Schema verified against
https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/ontology-definition
(not guessed -- an earlier example export in this repo's docs incorrectly
assumed an unverified format for a different Fabric item; this generator
follows the documented schema exactly).

Entity IDs and property IDs are deterministic (derived from a stable hash
of the name), so re-running this generator against an unchanged
ontology_config.json produces byte-identical output -- matching
generate_rdf.py's determinism.

Only 3 of the 8 Factory/Quality entities are bound here: `batch`,
`quality_check`, `line_status`. Verified live against this tenant:
`EventhouseTableDataBindingProperties` (`sourceType: KustoTable`) is
**only accepted when `dataBindingType` is `TimeSeries`** -- the REST API
docs say this explicitly, and a bare NonTimeSeries+KustoTable binding
fails with an opaque `ALMOperationImportFailed` error (its message
template is literally unfilled: "artifact '{1}' threw ... {2}"), only
diagnosed by bisecting down to a single entity type with no binding at
all, which DID succeed -- confirming the entity-type definition step
was fine and the binding step was the failure.

That rules out binding `factory`/`production_line`/`production_stage`/
`recipe` (no natural timestamp column, genuinely dimension/reference
data) directly from the Eventhouse -- they need a Lakehouse
(`LakehouseTableDataBindingProperties`) instead, which is also what
`RelationshipTypes/*/Contextualizations` require for relationship
*instances* (not just declared types) regardless of source. Both are a
follow-up once a Lakehouse item exists; `sensor_reading` is also
deferred (EAV shaped, and its source table's Timestamp column is
string, not datetime -- see demo/eventhouse/README.md's "Verified
against a live tenant" section).

Relationships are declared as RelationshipTypes only for now (no
Contextualizations -- see above).

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

# Table name -> (KQL source table, binding type, timestamp column).
# Eventhouse/KustoTable bindings are TimeSeries-only (verified live) --
# factory/production_line/production_stage/recipe are genuine dimension
# tables with no timestamp column, so they're deferred to a Lakehouse
# binding follow-up rather than forced in here.
FACTORY_QUALITY_BINDINGS = {
    "batch": ("silver_batch", "TimeSeries", "StartTime"),
    "quality_check": ("silver_quality_check", "TimeSeries", "Timestamp"),
    "line_status": ("silver_line_status", "TimeSeries", "Timestamp"),
}

# Live columns per source table (verified via `<table> | getschema` against
# the deployed Eventhouse -- richer than ontology_config.json's abstract
# columns in places, e.g. silver_quality_check's LineName/FactoryCode
# enrichment; narrower in others, e.g. silver_batch has no QuantityKg/Status).
LIVE_COLUMNS = {
    "silver_batch": {"BatchId": "String", "LineId": "String", "RecipeId": "String", "StartTime": "DateTime", "EndTime": "DateTime"},
    "silver_quality_check": {"Timestamp": "DateTime", "CheckId": "String", "BatchId": "String", "LineId": "String", "LineName": "String", "FactoryId": "String", "FactoryCode": "String", "StageId": "String", "StageName": "String", "Phase": "String", "DefectRate": "Double", "Result": "String", "Notes": "String"},
    "silver_line_status": {"Timestamp": "DateTime", "EventId": "String", "LineId": "String", "LineName": "String", "FactoryId": "String", "FactoryCode": "String", "Status": "String", "Reason": "String"},
}

# Preferred display-name column per table (falls back to the key column).
DISPLAY_NAME_COLUMN = {}

FACTORY_QUALITY_TABLES = list(FACTORY_QUALITY_BINDINGS)
FACTORY_QUALITY_RELATIONSHIPS = [
    r
    for r in CONFIG["relationships"]
    if r["from"] in FACTORY_QUALITY_TABLES and r["to"] in FACTORY_QUALITY_TABLES
]


def stable_id(name: str) -> str:
    """Deterministic positive ~60-bit ID from a name -- fits Fabric IQ's
    BigInt ID requirement without a stored counter/registry."""
    return str(int(hashlib.sha256(name.encode()).hexdigest()[:15], 16))


def property_id(table: str, column: str) -> str:
    return stable_id(f"{table}.{column}")


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)

    write_json(OUT / "definition.json", {})

    entity_type_id = {}
    for table in FACTORY_QUALITY_TABLES:
        entity_type_id[table] = stable_id(table)

    for table in FACTORY_QUALITY_TABLES:
        table_config = CONFIG["tables"][table]
        source_table, binding_type, timestamp_col = FACTORY_QUALITY_BINDINGS[table]
        columns = LIVE_COLUMNS[source_table]
        key_col = table_config["key"]

        eid = entity_type_id[table]

        def as_property(col, value_type):
            return {
                "id": property_id(table, col),
                "name": col,
                "redefines": None,
                "baseTypeNamespaceType": None,
                "valueType": value_type,
            }

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
        if binding_type == "TimeSeries":
            # Verified live: "Entity keys cannot be specified when the
            # entity type has no static properties. Entity keys can only
            # reference static properties." -- the key column (constant
            # per entity instance) must be a static `properties` entry,
            # not a `timeseriesProperties` one, even though its data
            # still comes from the same time-indexed KQL table.
            entity_type["properties"] = [as_property(key_col, columns[key_col])]
            entity_type["timeseriesProperties"] = [
                as_property(col, value_type)
                for col, value_type in columns.items()
                if col != key_col
            ]
        else:
            entity_type["properties"] = [
                as_property(col, value_type) for col, value_type in columns.items()
            ]

        write_json(OUT / "EntityTypes" / eid / "definition.json.tmpl", entity_type)

        data_binding_id = stable_id(f"{table}.binding")
        source_properties = {
            "sourceType": "KustoTable",
            "workspaceId": "{{ .WorkspaceId }}",
            "itemId": "{{ .EventhouseId }}",
            "clusterUri": "{{ .ClusterUri }}",
            "databaseName": "{{ .DatabaseName }}",
            "sourceTableName": source_table,
        }
        data_binding_config = {
            "dataBindingType": binding_type,
            "propertyBindings": [
                {"sourceColumnName": col, "targetPropertyId": property_id(table, col)}
                for col in columns
            ],
            "sourceTableProperties": source_properties,
        }
        if binding_type == "TimeSeries":
            data_binding_config["timestampColumnName"] = timestamp_col

        data_binding = {"id": data_binding_id, "dataBindingConfiguration": data_binding_config}
        write_json(
            OUT / "EntityTypes" / eid / "DataBindings" / f"{data_binding_id}.json.tmpl",
            data_binding,
        )

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

    n_entities = len(FACTORY_QUALITY_TABLES)
    n_rels = len(FACTORY_QUALITY_RELATIONSHIPS)
    print(f"generated {n_entities} entity types + {n_rels} relationship types -> {OUT}")
    print("(sensor_reading excluded -- EAV shape + string Timestamp, see module docstring)")
    print("(relationship instances/Contextualizations not yet wired -- verify live first)")


if __name__ == "__main__":
    main()
