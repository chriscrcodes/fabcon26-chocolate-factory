# demo

All chocolate-factory scenario code lives here, kept separate from the
vendored accelerators in `sources/` (each is its own upstream git repo —
we don't edit them in place).

- [`ontology/`](ontology) — chocolate scenario for `fabric-ontology`:
  `ontology_config.json` (all 3 agent domains), dimension `tables/*.csv`,
  `farm-preparation.md`. Deploys into
  `sources/fabric-ontology/data/scenarios/chocolate/`.
- [`data-generation/`](data-generation) — standalone Python data generator.
  Streams production-line telemetry (`sensor_reading`, `quality_check`,
  `batch_event`, `line_status`) to Azure Event Hub, matching
  `ontology/ontology_config.json` field-for-field. Not (yet) a patch to
  `sources/fabric-data-generation` — it's an independent producer that any
  Fabric Eventstream can consume.
- [`eventhouse/`](eventhouse) — Bronze/Silver/Gold KQL for the Eventhouse:
  raw ingestion tables, per-stage Silver pivots + dimension joins, and
  Gold rollups (one materialized view, three functions — see the
  medallion-layers design memo for why), plus `eventstream.json` routing
  the generator's four `RecordType`s into the four Bronze tables. Not yet
  run against a live Eventhouse; see `eventhouse/README.md` for what to
  verify first.
- `agents/` — not started. Coordinator + domain-specialist (Factory/Quality,
  Supply Chain, ERP/Orders) instructions and orchestration config for
  Microsoft Foundry.
- [`doc/`](doc) — design memos as repo-tracked markdown: `cacao-data-model.md`
  (factories, process, entity model) and `cacao-medallion-layers.md`
  (Bronze/Silver/Gold design + the Kusto-docs reality check).
- [`kb/`](kb) — unstructured knowledge-base documents for Foundry IQ:
  process glossary, sensor metric ranges, business-rule definitions per
  domain. Deploys alongside `ontology/` into `fabric-ontology`'s
  document indexing.
- [`infra/`](infra) — one Terraform state for both Azure (Event Hub) and
  Fabric (workspace, Eventhouse, KQL Database, Eventstream). Supports
  either a Fabric trial tenant (reference an existing workspace — trial
  capacities aren't supported by the provider) or a real Azure-provisioned
  capacity (create a new workspace, optionally with workspace identity).
  `terraform validate`/`plan` pass against both paths, not yet applied.
  README covers the full Fabric IaC landscape researched, and exactly
  which parts were deliberately left manual (the Event Hub connection
  itself, and running `eventhouse/01`–`03`'s KQL).

Nine production stages in three phases (Farm Preparation / Factory
Processing / Finishing) — Farm Preparation happens off-site near cocoa
origin, so only the six in-factory stages are modelled as
`production_stage` rows; see `ontology/farm-preparation.md`. Four factories
(EMEA-BCN Barcelona, NA-CHI Chicago, LATAM-GRU São Paulo, APAC-SIN
Singapore), 2-3 production lines each.

See [`doc/cacao-data-model.md`](doc/cacao-data-model.md) and
[`doc/cacao-medallion-layers.md`](doc/cacao-medallion-layers.md) for the
full entity model and rationale.
