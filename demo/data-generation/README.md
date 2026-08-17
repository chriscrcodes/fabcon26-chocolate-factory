# data-generation

Streams simulated production-line telemetry for the four chocolate factories
to Azure Event Hub, in the exact shape the `sensor_reading`, `quality_check`,
`batch`, and `line_status` tables define in
[`demo/ontology/ontology_config.json`](../ontology/ontology_config.json) --
so the ontology and the generator can never drift apart on field names.

## What it does

Each production line (2-3 per factory, see
[`src/seed_data.py`](src/seed_data.py)) cycles continuously through the six
in-factory stages defined in [`src/stage_catalog.py`](src/stage_catalog.py):
`grinding -> mixing_refining -> conching -> tempering -> molding_cooling ->
packaging`. Every tick, a line emits one of four record types
(see [`src/simulator.py`](src/simulator.py)):

- **`sensor_reading`** -- one row per metric for the line's current stage
  (EAV shape: `Metric`, `Value`, `Unit` -- adding a metric later needs no
  schema change)
- **`quality_check`** -- on the stage's last tick, with a `DefectRate`
  derived from how far that tick's readings sat outside normal range.
  Carries `LineId` directly (not just `BatchId`) so Silver can enrich it
  without joining through the async `silver_batch` materialized view.
- **`batch_event`** -- `Started` at the first tick of grinding, `Completed`
  at the last tick of packaging. This is what `silver_batch` should read
  instead of inferring timing from `sensor_reading`.
- **`line_status`** -- `Down` when a line enters a randomized maintenance
  window (`--downtime-rate`, default 1.5%/tick), `Running` when it exits.
  While down, a line emits nothing else -- no readings, no quality checks.
  Feeds Availability in a future `gold_factory_oee_daily`.

Farm Preparation (harvest/fermentation, drying/roasting, winnowing) is
intentionally not simulated here -- it happens off-site, near cocoa origin.
See [`../ontology/farm-preparation.md`](../ontology/farm-preparation.md).

## Setup

Uses [uv](https://docs.astral.sh/uv/) -- `pyproject.toml` is the source of
truth for dependencies, `uv sync` creates `.venv` and the lockfile.

```bash
uv sync
cp .env.sample .env   # fill in your Event Hub connection string or namespace
```

## Run

```bash
# 1. Write the dimension CSVs (factory, production_line, production_stage, recipe)
uv run run_seed_data.py

# 2. Stream telemetry
uv run run_simulator.py --interval 5 --anomaly-rate 0.03 --downtime-rate 0.015
```

`run_simulator.py` reads `AZURE_EVENT_HUB_CONNECTION_STRING` (+
`AZURE_EVENT_HUB_NAME`) if set, otherwise falls back to `az login` +
`AZURE_EVENT_HUB_NAMESPACE_HOSTNAME` + `AZURE_EVENT_HUB_NAME` -- same auth
pattern as `sources/fabric-data-generation`. Stop with Ctrl+C.

Verified against a real Event Hub: an 18-second bounded run
(`--max-runtime 18`) streamed 219 events across all four record types with
no connection errors.

## Linting

```bash
uv run ruff check .     # lint
uv run ruff format .    # format
```

## Not yet built

- `production_line`/`factory`/`production_stage`/`recipe` are static
  reference CSVs, not streamed -- `sensor_reading`, `quality_check`,
  `batch_event`, and `line_status` are the four live record types.
- Supply Chain and ERP/Orders entities (supplier, material, inventory,
  shipment, customer, product, sales_order, order_line, invoice) are
  defined in the ontology but have no generator yet.
- Bronze/Silver/Gold KQL and the Eventstream definition now live in
  [`../eventhouse/`](../eventhouse) -- written but not yet run against a
  live Eventhouse.
