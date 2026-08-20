#!/usr/bin/env python3
"""Generate a Fabric IQ Ontology item definition (EntityTypes/DataBindings/
RelationshipTypes) from ontology_config.json, covering Factory/Quality
(bound to the live Eventhouse) and Supply Chain/ERP (bound to a shared
dimension Lakehouse, alongside Factory/Quality's own dimension tables).

Schema verified against
https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/ontology-definition
(not guessed).

Entity IDs and property IDs are deterministic (derived from a stable hash
of the name), so re-running this generator against an unchanged
ontology_config.json produces byte-identical output -- matching
generate_rdf.py's determinism.

All of Factory/Quality's 8 entities (`sensor_reading` realized as 6
per-stage entities, see below) and all 9 Supply Chain/ERP entities are
bound here. Two real constraints found live shape which source each
uses:

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

So: `batch`, `quality_check`, `line_status`, and the 6 `sensor_reading_*`
stage entities (all genuinely time-indexed) bind to the Eventhouse as
TimeSeries; everything else -- Factory/Quality's dimension tables
(`factory`, `production_line`, `production_stage`, `recipe`) and all of
Supply Chain/ERP (`supplier`, `material`, `inventory`, `shipment`,
`customer`, `product`, `sales_order`, `order_line`, `invoice`) -- binds
NonTimeSeries via LakehouseTable. There is no
`SqlDatabaseTable`/`WarehouseTable` sourceType in the schema, so Supply
Chain/ERP's tables (whose CSVs already exist as the seed source for the
Fabric SQL Database) are mirrored into this same Lakehouse purely to
satisfy the Ontology's binding format --
`fabric/ontology/deploy_dimension_lakehouse.py` loads all of it (both
groups) from fabric/ontology/tables/*.csv as real Delta tables. The
Fabric SQL Database stays the actual system of record.

`sensor_reading` (ontology_config.json's single abstract EAV table) has
no direct binding -- entities need static-named properties, which an
EAV row (one per metric per tick) can't provide. `fabric/eventhouse/
02_silver.kql` already pivots it into 6 per-stage tables for KQL
consumers; this generator binds those 6 as 6 distinct TimeSeries entity
types instead (`STAGE_READING_TABLES`/`STAGE_METRIC_COLUMNS`), each
keyed by `LineId` alone -- the pivot leaves no per-reading ID column, so
identity here is "this line's readings at this stage over time," not a
discrete per-event ID like `batch`/`quality_check`/`line_status` use.

Relationship *instances* (`RelationshipTypes/*/Contextualizations`) need
a `LakehouseTableDataBindingProperties` source regardless of which
entities they relate -- an Eventhouse table can never be a
Contextualization source *directly* (`dataBindingTable` is always the
table owning the FK column pair, i.e. the relationship's "from" table).
Two ways a table clears this bar, tracked by `data_binding_source_table()`:
natively Lakehouse-bound (every Supply Chain/ERP table, plus
`production_line`/`factory`/`production_stage`/`recipe`), or
Eventhouse-bound with OneLake availability enabled
(`fabric/eventhouse/04_onelake_mirroring.kql`) and a matching Lakehouse
shortcut (`deploy_onelake_shortcuts.py`) -- covers `quality_check`,
`line_status`, and all 6 `sensor_reading_*` stage entities. Only `batch`
stays without a path to instance data: its source, `silver_batch`, is a
materialized view, and `.alter-merge table silver_batch policy
mirroring ...` fails live (the materialized-view variant of the command
doesn't parse); `batch_to_line` and `batch_to_recipe` are the two
relationships this leaves type-only. Converting the materialized view
into a form OneLake availability supports is a bigger follow-up, not
attempted here.

`sourceKeyRefBindings`/`targetKeyRefBindings` semantics (verified
against Microsoft Learn's own Contextualization example, a `contains`
RelationshipType joining two Equipment entities): each array names the
`dataBindingTable` column holding *that side's own key value*, mapped
to that side's key property -- not "the source table's column, mapped
to whichever property," and not keyed by the relationship's `fromKey`/
`toKey` naming symmetrically. Since `dataBindingTable` here is always
the "from" entity's own table, `sourceKeyRefBindings` is the trivial
case (the table's own key column, identifying itself), while
`targetKeyRefBindings` uses `rel["fromKey"]` -- the actual FK column
name present in that table, which is not always identical to
`rel["toKey"]` (`shipment_to_factory`'s FK column is `FromFactoryId`,
but `toKey` is `FactoryId` -- `shipment` has no column literally named
`FactoryId`). An earlier version of this code used `rel["fromKey"]`/
`rel["toKey"]` directly for both sides, which happened to produce valid
column references for every relationship where `fromKey == toKey`
(true almost everywhere in this config) but would have silently
generated a nonexistent column reference for `shipment_to_factory` --
caught only by re-deriving the semantics from Microsoft's own example,
not by anything erroring at deploy time.

Usage: uv run generate_fabric_iq_definition.py
Output: fabric/ontology/fabric_iq/ (definition.json, EntityTypes/, RelationshipTypes/)
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
#
# sensor_reading (ontology_config.json's abstract EAV table -- one row
# per metric per tick) has no direct Fabric IQ binding: entities need
# static-named properties, and Fabric IQ's own docs restrict a bound
# table to well-known columns, neither of which an EAV shape provides.
# fabric/eventhouse/02_silver.kql already solves exactly this for KQL
# consumers -- 6 per-stage tables, each pivoting sensor_reading's
# Metric/Value pairs into named columns for that stage's 3 metrics --
# so this generator binds those 6 pivoted tables as 6 distinct entity
# types instead of fighting the EAV shape. They don't exist in
# ontology_config.json (whose single "sensor_reading" row is the
# EAV-shape abstraction, unrealizable directly); STAGE_READING_TABLES/
# STAGE_METRIC_COLUMNS below are this generator's own addition, same
# as LIVE_COLUMNS already diverging from ontology_config.json's
# abstract columns for silver_quality_check's enrichment columns.
#
# Each pivoted table has no surviving per-reading ID column (pivot
# collapses multiple EAV rows into one wide row per LineId+Timestamp),
# so unlike batch/quality_check/line_status (each keyed by a genuine
# per-event ID), these are keyed by LineId alone -- "this line's
# readings at this stage over time," the natural TimeSeries shape for
# continuous sensor telemetry (as opposed to discrete events).
STAGE_READING_TABLES = [
    "grinding",
    "mixing_refining",
    "conching",
    "tempering",
    "molding_cooling",
    "packaging",
]
STAGE_METRIC_COLUMNS = {
    "grinding": {"ParticleSizeMicron": "Double", "MotorTemperatureC": "Double", "ThroughputKgPerHr": "Double"},
    "mixing_refining": {"ParticleSizeMicron": "Double", "RollerTemperatureC": "Double", "ViscosityPaS": "Double"},
    "conching": {"TemperatureC": "Double", "MoisturePercent": "Double", "AcidityPH": "Double"},
    "tempering": {"TemperatureC": "Double", "CrystalFormIndex": "Double", "ViscosityPaS": "Double"},
    "molding_cooling": {"MoldTemperatureC": "Double", "TunnelTemperatureC": "Double", "VibrationHz": "Double"},
    "packaging": {"LineSpeedUnitsPerMin": "Double", "SealTemperatureC": "Double", "RejectRatePercent": "Double"},
}

EVENTHOUSE_BINDINGS = {
    "batch": ("silver_batch", "StartTime"),
    "quality_check": ("silver_quality_check", "Timestamp"),
    "line_status": ("silver_line_status", "Timestamp"),
    **{
        f"sensor_reading_{stage}": (f"silver_{stage}", "Timestamp")
        for stage in STAGE_READING_TABLES
    },
}

# Key column for tables with no ontology_config.json entry (the 6
# per-stage reading tables above) -- everything else uses
# CONFIG["tables"][table]["key"], see key_column() below.
KEY_COLUMN_OVERRIDES = {f"sensor_reading_{stage}": "LineId" for stage in STAGE_READING_TABLES}

# Table name -> source table (same name as the Delta table loaded by
# deploy_dimension_lakehouse.py). NonTimeSeries, bound via LakehouseTable.
# Supply Chain/ERP tables are mirrored into the same Lakehouse for the
# same reason the dimension tables are: the Ontology definition schema
# has exactly two sourceType options (LakehouseTable, KustoTable/
# Eventhouse-TimeSeries-only) -- no SqlDatabaseTable/WarehouseTable type
# exists (checked against
# https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/ontology-definition
# and https://learn.microsoft.com/en-us/fabric/iq/ontology/how-to-bind-data).
# The Fabric SQL Database remains the actual system of record; this
# mirror exists solely to satisfy the Ontology's binding format.
LAKEHOUSE_BINDINGS = {
    "factory": "factory",
    "production_line": "production_line",
    "production_stage": "production_stage",
    "recipe": "recipe",
    "supplier": "supplier",
    "material": "material",
    "inventory": "inventory",
    "shipment": "shipment",
    "customer": "customer",
    "product": "product",
    "sales_order": "sales_order",
    "order_line": "order_line",
    "invoice": "invoice",
}

# Live columns per source table. Eventhouse ones verified via
# `<table> | getschema` against the deployed Eventhouse -- richer than
# ontology_config.json's abstract columns in places (e.g.
# silver_quality_check's LineName/FactoryCode enrichment), narrower in
# others (e.g. silver_batch has no QuantityKg/Status). Lakehouse ones
# are verified against the real Delta schema Spark's CSV auto-inference
# produced (read from each table's _delta_log, not guessed/assumed from
# ontology_config.json's abstract types) -- two surprises this uncovered:
# customer.CreditLimit infers as an integer (every generated value
# happens to be a round number, e.g. 10000, so the CSV never has a
# decimal point), and every *Date column infers as a bare date, not a
# timestamp (business_data.py writes `date.isoformat()`, no time
# component). The Ontology valueType enum has no separate Date/Int32
# type (only String/Boolean/DateTime/Object/BigInt/Double), so both map
# to DateTime and BigInt respectively -- the same two types already
# used elsewhere.
LIVE_COLUMNS = {
    "silver_batch": {"BatchId": "String", "LineId": "String", "RecipeId": "String", "StartTime": "DateTime", "EndTime": "DateTime"},
    "silver_quality_check": {"Timestamp": "DateTime", "CheckId": "String", "BatchId": "String", "LineId": "String", "LineName": "String", "FactoryId": "String", "FactoryCode": "String", "StageId": "String", "StageName": "String", "Phase": "String", "DefectRate": "Double", "Result": "String", "Notes": "String"},
    "silver_line_status": {"Timestamp": "DateTime", "EventId": "String", "LineId": "String", "LineName": "String", "FactoryId": "String", "FactoryCode": "String", "Status": "String", "Reason": "String"},
    "factory": {"FactoryId": "String", "Code": "String", "City": "String", "Country": "String", "Region": "String", "TimeZone": "String"},
    "production_line": {"LineId": "String", "FactoryId": "String", "LineNumber": "BigInt", "Name": "String"},
    "production_stage": {"StageId": "String", "Phase": "String", "Name": "String", "SequenceOrder": "BigInt"},
    "recipe": {"RecipeId": "String", "Name": "String", "CacaoPercent": "Double", "MilkPercent": "Double", "SugarPercent": "Double"},
    "supplier": {"SupplierId": "String", "Name": "String", "Country": "String", "MaterialType": "String", "Rating": "Double"},
    "material": {"MaterialId": "String", "SupplierId": "String", "Type": "String", "LotNumber": "String", "ReceivedDate": "DateTime", "QuantityKg": "Double"},
    "inventory": {"InventoryId": "String", "FactoryId": "String", "MaterialId": "String", "QuantityOnHand": "Double", "ReorderLevel": "Double", "LastUpdated": "DateTime"},
    "shipment": {"ShipmentId": "String", "FromFactoryId": "String", "ToLocationId": "String", "BatchId": "String", "Carrier": "String", "DepartDate": "DateTime", "ArriveDate": "DateTime", "Status": "String"},
    "customer": {"CustomerId": "String", "Name": "String", "Country": "String", "Segment": "String", "CreditLimit": "BigInt"},
    "product": {"ProductId": "String", "Name": "String", "RecipeId": "String", "PackagingType": "String", "SKU": "String"},
    "sales_order": {"OrderId": "String", "CustomerId": "String", "OrderDate": "DateTime", "Status": "String", "TotalAmount": "Double", "Currency": "String"},
    "order_line": {"OrderLineId": "String", "OrderId": "String", "ProductId": "String", "QuantityKg": "Double", "UnitPrice": "Double"},
    "invoice": {"InvoiceId": "String", "OrderId": "String", "IssueDate": "DateTime", "DueDate": "DateTime", "AmountDue": "Double", "PaidDate": "DateTime"},
    **{
        f"silver_{stage}": {
            "Timestamp": "DateTime",
            "LineId": "String",
            "LineName": "String",
            "FactoryId": "String",
            "FactoryCode": "String",
            "StageId": "String",
            "BatchId": "String",
            **metrics,
        }
        for stage, metrics in STAGE_METRIC_COLUMNS.items()
    },
}

# Preferred display-name column per table (falls back to the key column).
DISPLAY_NAME_COLUMN = {
    "factory": "Code",
    "production_line": "Name",
    "production_stage": "Name",
    "recipe": "Name",
    "supplier": "Name",
    "customer": "Name",
    "product": "Name",
}

ALL_TABLES = list(EVENTHOUSE_BINDINGS) + list(LAKEHOUSE_BINDINGS)

# The 6 sensor_reading_<stage> tables have no ontology_config.json entry
# (see EVENTHOUSE_BINDINGS' comment), so their relationships -- mirroring
# ontology_config.json's reading_to_line/reading_to_batch for the single
# abstract sensor_reading table -- are added here by hand rather than
# derived from CONFIG. reading_to_stage has no per-stage equivalent: the
# stage is now implicit in which entity type a reading belongs to, not a
# separate runtime relationship.
STAGE_READING_RELATIONSHIPS = [
    rel
    for stage in STAGE_READING_TABLES
    for rel in (
        {
            "name": f"reading_{stage}_to_line",
            "from": f"sensor_reading_{stage}",
            "to": "production_line",
            "fromKey": "LineId",
            "toKey": "LineId",
        },
        {
            "name": f"reading_{stage}_to_batch",
            "from": f"sensor_reading_{stage}",
            "to": "batch",
            "fromKey": "BatchId",
            "toKey": "BatchId",
        },
    )
]

ALL_RELATIONSHIPS = [
    r for r in CONFIG["relationships"] if r["from"] in ALL_TABLES and r["to"] in ALL_TABLES
] + STAGE_READING_RELATIONSHIPS

# Eventhouse tables with OneLake availability enabled
# (fabric/eventhouse/04_onelake_mirroring.kql) and a matching Lakehouse
# shortcut (fabric/ontology/deploy_onelake_shortcuts.py) -- table name ->
# shortcut name in the dimension Lakehouse (a shortcut is transparent to
# consumers, so it binds exactly like a native LakehouseTable). Excludes
# `batch`: `silver_batch` is a materialized view, and
# `.alter-merge table silver_batch policy mirroring ...` fails live (the
# materialized-view variant of the command doesn't parse at all) --
# batch_to_line/batch_to_recipe stay type-only; converting the
# materialized view into a form OneLake availability supports is a
# bigger follow-up, not attempted here.
EVENTHOUSE_MIRROR_SHORTCUTS = {
    "quality_check": "silver_quality_check",
    "line_status": "silver_line_status",
    **{f"sensor_reading_{stage}": f"silver_{stage}" for stage in STAGE_READING_TABLES},
}


def data_binding_source_table(table: str) -> str | None:
    """The Lakehouse-area table name (native or shortcut) a relationship's
    "from" table can be Contextualized through, or None if it can't be."""
    if table in LAKEHOUSE_BINDINGS:
        return LAKEHOUSE_BINDINGS[table]
    return EVENTHOUSE_MIRROR_SHORTCUTS.get(table)


# Relationship instances (Contextualizations): a relationship gets one
# whenever its "from" table (the FK owner -- dataBindingTable is always
# the table owning the FK column pair, not necessarily either entity's
# own table in general, though it is for every relationship here) has a
# Lakehouse-area table to bind through -- either it's natively
# Lakehouse-bound, or it's Eventhouse-bound with a OneLake-mirror
# shortcut (above). Eventhouse tables can never be a Contextualization
# source *directly* -- see module docstring. Since every Supply
# Chain/ERP table is Lakehouse-bound, all 10 of their relationships
# qualify, including shipment_to_batch (shipment owns the FK; batch
# being Eventhouse-bound only matters for *source* tables, not targets).
CONTEXTUALIZED_RELATIONSHIPS = {
    rel["name"] for rel in ALL_RELATIONSHIPS if data_binding_source_table(rel["from"]) is not None
}


def stable_id(name: str) -> str:
    """Deterministic positive ~60-bit ID from a name -- fits Fabric IQ's
    BigInt ID requirement without a stored counter/registry."""
    return str(int(hashlib.sha256(name.encode()).hexdigest()[:15], 16))


def property_id(table: str, column: str) -> str:
    return stable_id(f"{table}.{column}")


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")


def key_column(table: str) -> str:
    if table in KEY_COLUMN_OVERRIDES:
        return KEY_COLUMN_OVERRIDES[table]
    return CONFIG["tables"][table]["key"]


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

    entity_type_id = {table: stable_id(table) for table in ALL_TABLES}

    for table in ALL_TABLES:
        is_time_series = table in EVENTHOUSE_BINDINGS
        source_table = (
            EVENTHOUSE_BINDINGS[table][0] if is_time_series else LAKEHOUSE_BINDINGS[table]
        )
        columns = LIVE_COLUMNS[source_table]
        key_col = key_column(table)

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
    for rel in ALL_RELATIONSHIPS:
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
                    "sourceTableName": data_binding_source_table(from_table),
                },
                # Per Microsoft Learn's Contextualization example (a
                # `contains` RelationshipType binding two Equipment
                # entities through a join table): sourceKeyRefBindings
                # names the dataBindingTable column holding the SOURCE
                # entity's own key value; targetKeyRefBindings names the
                # column holding the TARGET's key value. Since
                # dataBindingTable is always the "from" entity's own
                # table here, the source-side column is that table's own
                # key column (key_column(from_table)) -- not rel["fromKey"],
                # which is the *foreign*-key column pointing at the
                # target. Verified live this distinction actually
                # matters, not just cosmetic: shipment_to_batch and
                # shipment_to_factory have fromKey ("BatchId"/
                # "FromFactoryId") that differs from toKey
                # ("BatchId"/"FactoryId") -- shipment's own table has no
                # "FactoryId" column, only "FromFactoryId", so binding
                # target-side off rel["toKey"] (the earlier, incorrect
                # version of this code) would reference a column that
                # doesn't exist on the source table at all.
                "sourceKeyRefBindings": [
                    {
                        "sourceColumnName": key_column(from_table),
                        "targetPropertyId": property_id(from_table, key_column(from_table)),
                    }
                ],
                "targetKeyRefBindings": [
                    {
                        "sourceColumnName": rel["fromKey"],
                        "targetPropertyId": property_id(to_table, key_column(to_table)),
                    }
                ],
            }
            write_json(
                OUT / "RelationshipTypes" / rid / "Contextualizations" / f"{contextualization_id}.json.tmpl",
                contextualization,
            )
            n_contextualizations += 1

    n_entities = len(ALL_TABLES)
    n_rels = len(ALL_RELATIONSHIPS)
    print(f"generated {n_entities} entity types + {n_rels} relationship types "
          f"({n_contextualizations} with instance data) -> {OUT}")
    print("(sensor_reading bound as 6 per-stage entities, see module docstring)")


if __name__ == "__main__":
    main()
