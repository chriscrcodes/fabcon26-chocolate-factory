# eventhouse

Bronze/Silver/Gold KQL for the chocolate factory Eventhouse, matching the
design in the medallion-layers design memo and the schema in
[`../ontology/ontology_config.json`](../ontology/ontology_config.json).

## Deploy order

Run against a KQL queryset in the target Eventhouse database, in order:

1. **[`01_bronze_and_reference.kql`](01_bronze_and_reference.kql)** — 4
   Bronze tables (one per `RecordType` the generator streams), 4 reference
   tables (native copies of the seeded CSVs — never a OneLake shortcut),
   and the `streamingingestion` disables that the Silver joins below need.
2. **[`02_silver.kql`](02_silver.kql)** — 6 stage-pivot tables (update
   policy, pivots the EAV readings + joins in dimensions), enriched
   `silver_quality_check` and `silver_line_status`, and `silver_batch`
   (the one true materialized view — single source, no join).
3. **[`03_gold.kql`](03_gold.kql)** — `gold_defect_rate_by_stage_daily`
   (a real materialized view) plus three functions
   (`gold_line_throughput_hourly()`, `gold_batch_summary()`,
   `gold_factory_oee_daily()`) that join across Silver tables at query
   time, since Kusto materialized views can't have more than one source.

4. **[`eventstream.json`](eventstream.json)** — the Eventstream item
   definition: one `AzureEventHub` source, a `Filter` operator per
   `RecordType` (`sensor_reading`/`quality_check`/`batch_event`/
   `line_status`), and one `Eventhouse` destination per Bronze table.
   Column lists in each destination's `inputSchema` match the Bronze
   table schemas and the generator's payload fields exactly (verified
   programmatically — see below).

### Deploying `eventstream.json`

Fill in the placeholders before creating the item:

- `<EVENT_HUB_CONNECTION_ID>` — the Fabric **Connection** resource
  pointing at the Event Hub namespace (Workspace settings → Manage
  connections and gateways → create one for the namespace if it doesn't
  exist yet).
- `<WORKSPACE_ID>` — the target workspace's GUID (from its URL).
- `<KQL_DATABASE_ITEM_ID>` — the KQL database item's own GUID (its
  Settings, or its URL) -- **not** the parent Eventhouse's GUID; the
  Eventstream destination's `itemId` must resolve directly to a
  queryable database, and passing the Eventhouse's ID instead fails
  with "Unable to extract cluster URL from the Eventhouse KQL database
  item ID ..." (verified against a live tenant).
- `<KQL_DATABASE_NAME>` — the KQL database name inside that Eventhouse
  (the one `01`–`03` were run against).

Then either import via the Fabric UI (New item → Eventstream → Edit as
JSON / paste this definition) or create it via the
[Eventstream REST API](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/eventstream-rest-api)
with this file as the `eventstream.json` definition part. Once live,
start `demo/data-generation/run_simulator.py` and events should start
landing in the four Bronze tables.

The `Filter` operator shape here (`operatorType`/`ColumnReference`/
`Literal`) is taken directly from Microsoft's own
[fabric-event-streams](https://github.com/microsoft/fabric-event-streams)
template repo (`API Templates/eventstream-definition.json`), not
reverse-engineered — this is the one part of the whole pipeline checked
against a real reference example rather than docs prose alone.

## What's not verified yet

This KQL was written against the documented syntax (Kusto Learn docs,
cross-checked in the design memo's Reality check section) but **has not
been run against a live Eventhouse** — there's no Kusto engine in this
environment to test against. Before a real run, check these first, in
rough order of risk:

- **`evaluate pivot(Metric, take_any(Value))`** in the 6 stage transforms
  — the pivot plugin's exact column-inference behavior is the least
  battle-tested part of this file. If it doesn't group by
  `LineId, StageId, BatchId, Timestamp` as intended, each metric may land
  in its own row instead of pivoting into columns.
- **`prev()` after `serialize`** in `gold_factory_oee_daily()` — the
  Down/Running pairing logic. Verify the window actually resets correctly
  per `LineId` (the `LineId == PrevLineId` guard should handle the
  boundary, but this is exactly the kind of thing to check against real
  data first).
- **Join column suffixing** in the Gold functions — Kusto renames
  colliding non-key columns from the right side of a `join` with a `1`
  suffix. `gold_line_throughput_hourly()`'s `LineName` should survive
  unambiguously since it's identical on both sides, but worth a quick
  eyeball on first run.
- Everything else (table/function creation, update policy wiring,
  `silver_batch`'s materialized view) follows patterns lifted close to
  verbatim from the official examples in the Kusto docs, so it's lower
  risk.
