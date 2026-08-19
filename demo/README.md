# demo

All chocolate-factory scenario code lives here, kept separate from the
vendored accelerators in `sources/` (each is its own upstream git repo —
we don't edit them in place).

- [`ontology/`](ontology) — chocolate scenario: `ontology_config.json`
  (all 3 agent domains), dimension `tables/*.csv`, `farm-preparation.md`.
  Deployed as a real **Fabric IQ Ontology** item (preview), bound to the
  live Eventhouse and a small dimension Lakehouse — see
  `ontology/README.md`'s "Deploying to Fabric IQ" section.
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
  the generator's four `RecordType`s into the four Bronze tables. Deployed
  and verified end to end against a live Eventhouse — see
  `eventhouse/README.md`'s "Verified against a live tenant" section for
  the issues that surfaced only from a real run and how they were fixed.
- `agents/` — not started. Coordinator + domain-specialist (Factory/Quality,
  Supply Chain, ERP/Orders) instructions and orchestration config for
  Microsoft Foundry.
- [`doc/`](doc) — design memos as repo-tracked markdown: `cacao-data-model.md`
  (factories, process, entity model) and `cacao-medallion-layers.md`
  (Bronze/Silver/Gold design + the Kusto-docs reality check).
- [`kb/`](kb) — unstructured knowledge-base documents for Foundry IQ:
  process glossary, sensor metric ranges, business-rule definitions per
  domain. Planned to deploy as a Foundry IQ knowledge source pointed
  directly at OneLake files (no Azure AI Search indexing needed) — not
  built yet, see `kb/README.md`.
- [`infra/`](infra) — one Terraform state for Azure (Event Hub) and
  Fabric (capacity, workspace, Eventhouse, KQL Database, Connection,
  Eventstream, dimension Lakehouse, Fabric IQ Ontology). Provisions a
  real Azure Fabric capacity (F2 by default) rather than assuming one
  exists. Deployed and verified against a real tenant — see
  `infra/README.md`.

Nine production stages in three phases (Farm Preparation / Factory
Processing / Finishing) — Farm Preparation happens off-site near cocoa
origin, so only the six in-factory stages are modelled as
`production_stage` rows; see `ontology/farm-preparation.md`. Four factories
(EMEA-BCN Barcelona, NA-CHI Chicago, LATAM-GRU São Paulo, APAC-SIN
Singapore), 2-3 production lines each.

See [`doc/cacao-data-model.md`](doc/cacao-data-model.md) and
[`doc/cacao-medallion-layers.md`](doc/cacao-medallion-layers.md) for the
full entity model and rationale.
