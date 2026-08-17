# Cacao Medallion Layers

FabCon Europe 2026 · Design memo

Table-and-policy design, now built as real KQL in `demo/eventhouse/`.
`sources/fabric-data-generation` had no real medallion to preserve — one
raw `events` table, no update policies, no Lakehouse. This was a
from-scratch design for the streaming and batch data our own generator
produces.

## 0. Two data planes

Medallion tiering earns its keep where data arrives fast and raw. That's
Factory/Quality telemetry. Supply Chain and ERP/Orders are low-volume and
already typed at the source — they get a lighter Bronze→Silver→Gold
parallel inside the Fabric SQL DB `fabric-ontology` already provisions,
not a second Eventhouse pipeline.

| Plane | Domain | Mechanism |
|---|---|---|
| Eventhouse / KQL DB | Factory/Quality — streaming | High-frequency `sensor_reading` + `quality_check` from `demo/data-generation`. Update policies + materialized views do the tiering |
| Fabric SQL Database | Supply Chain & ERP/Orders — batch | Low-volume dimensional/transactional tables, already typed in `ontology_config.json`. Tiering is CSV load → typed table → SQL view |

## 1. Flow

```
data-generation --Event Hub-->  Bronze  --update policy: pivot + join dims-->  Silver
                                                                                   |
                                                          materialized view: rollups
                                                                                   v
                                                                                 Gold
                                                                                   |
                                                                  Factory/Quality agent
                                                                    (queries Gold only)
```

Update policies transform each incoming row as it lands — right
mechanism for the Bronze→Silver pivot. Gold rollups (grouping across many
rows over time) use Kusto **materialized views** instead, which maintain
an incremental aggregate rather than re-scanning history on every query
— except where a Gold table needs more than one source (see §5).

## 2. Bronze — raw ingest, unmodified

Exactly the shape `demo/data-generation/src/simulator.py` emits —
Eventstream routes by `RecordType` into four tables, no transform.
Reference tables load once from the seeded CSVs as **native ingested
tables**, never a OneLake shortcut (see §5).

| Table | Mechanism | Source | Notes |
|---|---|---|---|
| `bronze_sensor_reading` | Eventstream | `RecordType = sensor_reading` | EAV: LineId, StageId, BatchId, Timestamp, Metric, Value, Unit. `streamingingestion` disabled — its Silver update policies join dimensions |
| `bronze_quality_check` | Eventstream | `RecordType = quality_check` | BatchId, LineId, StageId, Timestamp, DefectRate, Result, Notes. Same `streamingingestion` note |
| `bronze_batch_event` | Eventstream | `RecordType = batch_event` | BatchId, LineId, RecipeId, EventType (Started/Completed), Timestamp |
| `bronze_line_status` | Eventstream | `RecordType = line_status` | LineId, Status (Running/Down), Reason, Timestamp. `streamingingestion` disabled — its Silver update policy joins dimensions |
| `ref_factory` | CSV load | `ontology/tables/factory.csv` | 4 rows, native table |
| `ref_production_line` | CSV load | `ontology/tables/production_line.csv` | 10 rows, native table |
| `ref_production_stage` | CSV load | `ontology/tables/production_stage.csv` | 6 rows, Phase-tagged, native table |
| `ref_recipe` | CSV load | `ontology/tables/recipe.csv` | 3 rows, native table |

## 3. Silver — pivoted per stage, dimensions joined in

Bronze is EAV by design (a new metric needs no schema change), but
that's awkward to query. Silver pivots `bronze_sensor_reading` into one
wide table per stage — each stage has a different metric set, so a
single union table would be mostly nulls. `silver_batch` reads the
explicit lifecycle events in `bronze_batch_event` instead of inferring
timing from readings.

| Table | Mechanism | Reads from | Adds |
|---|---|---|---|
| `silver_grinding` | update policy | `bronze_sensor_reading` | ParticleSizeMicron, MotorTemperatureC, ThroughputKgPerHr + FactoryCode, LineName |
| `silver_mixing_refining` | update policy | `bronze_sensor_reading` | ParticleSizeMicron, RollerTemperatureC, ViscosityPaS + FactoryCode, LineName |
| `silver_conching` | update policy | `bronze_sensor_reading` | TemperatureC, MoisturePercent, AcidityPH + FactoryCode, LineName |
| `silver_tempering` | update policy | `bronze_sensor_reading` | TemperatureC, CrystalFormIndex, ViscosityPaS + FactoryCode, LineName |
| `silver_molding_cooling` | update policy | `bronze_sensor_reading` | MoldTemperatureC, TunnelTemperatureC, VibrationHz + FactoryCode, LineName |
| `silver_packaging` | update policy | `bronze_sensor_reading` | LineSpeedUnitsPerMin, SealTemperatureC, RejectRatePercent + FactoryCode, LineName |
| `silver_quality_check` | update policy | `bronze_quality_check` | + FactoryCode, LineName, StageName, Phase |
| `silver_line_status` | update policy | `bronze_line_status` | + FactoryCode, LineName |
| `silver_batch` | **materialized view** | `bronze_batch_event` | StartTime/EndTime/Status straight from the Started/Completed events — no inference |

## 4. Gold — what the agents actually query

Pre-aggregated, semantically named, cheap to query — the Factory/Quality
specialist should never touch Bronze or Silver directly. One correction
from §5: a materialized view can only have one physical source table,
and can't sit on top of another materialized view. Three of these tables
need two-or-more sources, so they're built as **stored functions**
(joined live at query time) instead of MVs — fine at demo data volume;
only `gold_defect_rate_by_stage_daily` has a single source and stays a
true MV.

| Table / function | Grain | Mechanism | Reads from | Feeds |
|---|---|---|---|---|
| `gold_defect_rate_by_stage_daily` | Factory × Stage × Day | materialized view | `silver_quality_check` | Factory/Quality agent — "where are we losing chocolate?" |
| `gold_line_throughput_hourly()` | Line × Hour | function | `silver_grinding`, `silver_packaging` | Factory/Quality agent — capacity questions |
| `gold_batch_summary()` | one row per Batch | function | `silver_batch`, `silver_quality_check` | Factory/Quality agent, Coordinator (BatchId is a shared key) |
| `gold_factory_oee_daily()` | Factory × Day | function | `silver_line_status`, `silver_grinding`, `silver_packaging`, `silver_quality_check` | Coordinator — real Availability × Performance × Quality |

### Silver/Gold — Fabric SQL Database (Supply Chain & ERP/Orders)

Not yet generated, sketched for symmetry:

| Table | Tier | Mechanism | Feeds |
|---|---|---|---|
| supplier, material, inventory, shipment | Silver | typed load | already the `ontology_config.json` shape |
| customer, product, sales_order, order_line, invoice | Silver | typed load | already the `ontology_config.json` shape |
| `gold_inventory_position` | Gold | SQL view | Supply Chain agent — stock vs. reorder level, per factory/material |
| `gold_order_fulfillment_kpi` | Gold | SQL view | ERP/Orders agent — on-time %, backlog value |
| `gold_supplier_scorecard` | Gold | SQL view | Supply Chain agent — rating × lead time × lot count |

## 5. Reality check: what Kusto's own docs actually allow

Checked against Microsoft Learn before writing any KQL. Two of these
four changed the table list above; two are constraints satisfied by
construction.

### 5.1 Shortcuts can't back an MV or a joining update policy — confirmed

> "Materialized views can't be defined over external tables." ·
> "[An update policy] can't access external data or external tables."

- **Bites:** the four reference tables that every Silver update policy
  joins against.
- **Fix:** load them as native ingested tables from the seeded CSVs —
  the plan in §2 — and never swap that for a OneLake shortcut to save a
  step.
- Sources: [Materialized views limitations](https://learn.microsoft.com/en-us/kusto/management/materialized-views/materialized-views-limitations?view=microsoft-fabric),
  [Update policy overview](https://learn.microsoft.com/en-us/kusto/management/update-policy?view=microsoft-fabric)

### 5.2 An MV has exactly one source table — and can't sit on another MV — confirmed, plus an extra constraint

> "A materialized view can't be created on top of another materialized
> view, unless the first materialized view is of type `take_any(*)`
> aggregation."

- **Bites:** `gold_line_throughput_hourly` and `gold_batch_summary` (two
  Silver sources each), and `gold_factory_oee_daily` worst of all — it
  was drafted reading two other Gold materialized views directly, which
  is disallowed independent of the multi-source problem.
- **Fix:** all three became stored **functions** that join live at query
  time (§4) instead of physicalized MVs. `gold_factory_oee_daily()` now
  reads Silver tables directly rather than chaining off other Gold
  objects. Revisit physicalization only if query latency actually
  becomes a problem at demo scale — it won't.
- Source: [Materialized views limitations](https://learn.microsoft.com/en-us/kusto/management/materialized-views/materialized-views-limitations?view=microsoft-fabric)

### 5.3 Joining update policies need streaming ingestion off, upstream too — confirmed, on by default in Fabric

> "By default, the Streaming ingestion policy is enabled for all tables
> in the Eventhouse. To use functions with the join operator in an
> update policy, the streaming ingestion policy must be disabled." —
> cascades to every upstream table in the chain.

- **Bites:** `bronze_sensor_reading`, `bronze_quality_check`, and
  `bronze_line_status` — every Silver update policy reading them joins
  in dimension data.
- **Fix:** `.alter table <name> policy streamingingestion disable` on
  all three. Costs nothing here — Eventstream's default write cadence
  into Eventhouse is already batched, not the special low-latency
  streaming mode, so demo latency is unaffected.
- Source: [Update policy overview](https://learn.microsoft.com/en-us/kusto/management/update-policy?view=microsoft-fabric)
  (Fabric moniker section)

### 5.4 OneLake availability mirrors Eventhouse → Delta, read-only — confirmed, one-directional

> "You can create a logical copy of KQL database data in an eventhouse
> by turning on OneLake availability... query the data in your KQL
> database in Delta Lake format through other Fabric engines such as
> Direct Lake mode in Power BI, Warehouse, Lakehouse, Notebooks."

- **Bites:** nothing in the current design — agent-level joins were
  chosen over mirroring for the cross-plane decision (§6).
- **Relevance:** the concrete mechanism if that decision gets revisited:
  per-table or per-database toggle, adaptive write latency 5 min–3 hr,
  read-only Delta copy, reachable via a Lakehouse/Warehouse shortcut or
  the database's SQL endpoint. It only moves data *out of* Eventhouse —
  the SQL DB side would need its own export path to go the other way.
- Source: [Turn on OneLake availability for an eventhouse](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-house-onelake-availability)

## 6. Decisions

1. **Cross-plane queries → agent-level join, no mirroring.** The
   Coordinator calls the Factory/Quality specialist (Eventhouse) and the
   Supply Chain / ERP specialists (SQL DB) separately and synthesizes
   the answer itself. No OneLake shortcut, no unified query surface —
   revisit only if the demo script needs a genuinely single-query
   cross-domain answer.
2. **silver_batch freshness → explicit lifecycle events.** The generator
   emits a `bronze_batch_event` stream (BatchId, LineId, RecipeId,
   EventType: Started/Completed, Timestamp), at the first tick of
   grinding and the last tick of packaging. `silver_batch` is a clean
   materialized view over that — no scanning readings to infer status.
3. **Retention → one uniform policy, no per-tier tuning.** Same
   retention (e.g. 30 days) across Bronze/Silver/Gold. Differentiated
   TTL is a production storage-cost concern that doesn't apply at demo
   data volumes.
4. **OEE uptime → synthetic downtime signal in the generator.** Lines
   occasionally enter a "Down" maintenance window, streamed via
   `bronze_line_status`. Chosen over dropping Availability entirely
   because it gives the demo a real narrative beat — a line goes down,
   the Factory/Quality agent surfaces it, OEE dips and recovers on
   camera.

## 7. Implementation status

KQL lives in `demo/eventhouse/`:

- `01_bronze_and_reference.kql` — Bronze + reference tables,
  `streamingingestion` disables
- `02_silver.kql` — shared `EnrichLine()` lookup function, 6 stage-pivot
  tables, `silver_quality_check`, `silver_line_status`, `silver_batch`
- `03_gold.kql` — `gold_defect_rate_by_stage_daily` (MV),
  `gold_line_throughput_hourly()`, `gold_batch_summary()`,
  `gold_factory_oee_daily()` (functions)
- `eventstream.json` — the Eventstream item definition: one
  `AzureEventHub` source, a `Filter` operator per `RecordType`, four
  `Eventhouse` destinations. Filter operator shape verified against
  Microsoft's own `fabric-event-streams` template repo, not
  reverse-engineered from docs prose.

**Not yet run against a live Eventhouse.** `demo/eventhouse/README.md`
lists what to verify first, ranked by risk: the `pivot` plugin's exact
column-inference behavior, the `serialize`+`prev()` Down/Running pairing
in the OEE function, and join-column suffixing in the Gold functions.
