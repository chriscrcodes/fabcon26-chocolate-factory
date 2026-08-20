# eventhouse

Bronze/Silver/Gold KQL for the chocolate factory Eventhouse, matching the
design in the medallion-layers design memo and the schema in
[`../ontology/ontology_config.json`](../ontology/ontology_config.json).

## Deploy order

`../../infra`'s `terraform apply` runs all of this automatically, in order,
via `null_resource.load_kql`'s `local-exec` provisioner
([`run_kql.py`](run_kql.py) — see `../../infra/README.md`'s "Deploy"
section). The order, for anyone iterating on the KQL directly against a
queryset instead:

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
   Both materialized views use `.create-or-alter`, not `.create`, so
   `run_kql.py`/a re-`apply` can redeploy them without an
   `EntityAlreadyExistsException` (`.create materialized-view` isn't
   idempotent — hit this live before switching).

4. **[`eventstream.json`](eventstream.json)** — the Eventstream item
   definition: one `AzureEventHub` source, a `Filter` operator per
   `RecordType` (`sensor_reading`/`quality_check`/`batch_event`/
   `line_status`), and one `Eventhouse` destination per Bronze table.
   Column lists in each destination's `inputSchema` match the Bronze
   table schemas and the generator's payload fields exactly (verified
   programmatically — see below). `../../infra`'s `fabric.tf` creates this
   item automatically (Fabric Connection + Eventstream), filling in its
   placeholders itself — see there for the manual-deploy fallback if
   Terraform isn't being used.

5. **[`run_kql.py`](run_kql.py)** also loads
   `fabric/ontology/tables/*.csv` (written by
   `simulator/run_seed_data.py`) into the four `ref_*` tables
   — skipped per-table once it already has rows, so re-running is safe.
   Without this, Silver enrichment columns (`LineName`, `FactoryCode`,
   ...) stay blank. Doing this by hand instead:
   `.ingest inline into table ref_factory <| ...` per table works for
   local/dev use; a real deployment should use a Fabric pipeline Copy
   activity instead.
6. **[`04_onelake_mirroring.kql`](04_onelake_mirroring.kql)** enables
   OneLake availability (mirroring to Delta Parquet) on
   `silver_quality_check`, `silver_line_status`, and the 6 per-stage
   tables — so `fabric/ontology/deploy_onelake_shortcuts.py` can expose
   them as ordinary Lakehouse tables for the Fabric IQ Ontology's
   relationship instances (Eventhouse tables can never be a
   Contextualization source directly). `silver_batch` is excluded: it's
   a materialized view, and the mirroring policy command only accepts
   `table`, not `materialized-view` (`.alter-merge materialized-view
   silver_batch policy mirroring ...` fails to parse at all;
   `.alter-merge table silver_batch policy mirroring ...` returns "the
   requested endpoint ... does not exist"). See
   `fabric/ontology/generate_fabric_iq_definition.py`'s docstring for
   which relationships this leaves type-only.

The `Filter` operator shape here (`operatorType`/`ColumnReference`/
`Literal`) is taken directly from Microsoft's own
[fabric-event-streams](https://github.com/microsoft/fabric-event-streams)
template repo (`API Templates/eventstream-definition.json`), not
reverse-engineered — this is the one part of the whole pipeline checked
against a real reference example rather than docs prose alone.

## Verified against a live tenant

`01`–`03` have been run end to end against a real Fabric Eventhouse, with
the simulator actually streaming through Event Hub → Eventstream →
Bronze → Silver → Gold. Four issues only surfaced this way, all fixed in
the KQL here:

- **Bronze `Timestamp` is `string`, not `datetime`.** Eventstream's
  `ProcessedIngestion` auto-creates the Bronze tables from
  `eventstream.json`'s `inputSchema`, which types `Timestamp` as
  `Nvarchar(max)` (JSON has no native datetime) — and since `.create
  table` is a no-op against an already-existing table regardless of
  schema mismatches, `01`'s original `Timestamp: datetime` declaration
  silently never took effect. `01` now declares `Timestamp: string` to
  match reality, and every Silver transform casts with `todatetime(...)`.
- **`evaluate pivot()` can silently drop columns.** Update policies
  process one small ingestion batch/extent at a time; if a batch happens
  to carry zero rows for a given stage, `pivot()` produces no columns for
  that stage's metrics at all, and a plain `project Timestamp, ...,
  TemperatureC, ...` then fails to resolve the column — which, since
  these are `IsTransactional` policies, rolls back the *entire* ingestion
  batch and silently blocks Bronze itself from growing (visible via
  `.show ingestion failures`, not from the `.alter table policy update`
  command succeeding). Fixed by wrapping every pivoted metric column in
  `column_ifexists('X', real(null))` in the final `project` of each of
  the 6 stage transforms.
- **Materialized views only allow a table reference + one trailing
  `summarize`.** `silver_batch`'s original `| extend Status = ...` after
  the `summarize` was rejected outright; folding it into the `summarize`
  as an aggregation didn't work either (`iff` isn't a supported
  aggregation function for materialized views). Fixed by dropping
  `Status` from `silver_batch` entirely and deriving it downstream in
  `gold_batch_summary()` instead (a function, not an MV, so no such
  restriction).
- **`coalesce` needs matching branch types.** `gold_factory_oee_daily()`'s
  `coalesce(TotalDownMinutes, 0.0)` mixed `long` (from
  `datetime_diff`/`sum`) with `real` (`0.0`) — fixed with an explicit
  `toreal(TotalDownMinutes)`.

Two things this pass didn't verify: `gold_factory_oee_daily()`'s
`prev()`/`serialize` Down/Running pairing logic on a realistic multi-day
window (only tested against a few minutes of live data), and Gold
functions' behavior once `join` columns actually collide across Silver
tables at scale.

**OneLake mirroring latency**: `TargetLatencyInMinutes=5` is a target,
not a guarantee — Microsoft's own docs cite up to 3 hours in the worst
case. Enabling the policy and creating the shortcut both succeed
immediately and the table's schema appears in OneLake right away, but
the first real data commit can take noticeably longer than 5 minutes in
practice; query the shortcut's row count (or check for `.parquet` files
under `Tables/<name>/` via the OneLake DFS API) rather than assuming
data is present immediately after enabling.
