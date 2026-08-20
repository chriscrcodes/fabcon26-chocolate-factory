# fabcon26-chocolate-factory

A FabCon demo: a chocolate factory scenario spanning Microsoft Fabric
(Real-Time Intelligence, Fabric IQ Ontology, Fabric SQL Database) and
Microsoft Foundry (Foundry IQ knowledge bases, agents), kept separate
from the vendored accelerators in `sources/` (each is its own upstream
git repo — we don't edit them in place).

Organized by the three conceptual layers of the demo:

## `simulator/` — the data source

[`simulator/`](simulator) — standalone Python data generator. Streams
production-line telemetry (`sensor_reading`, `quality_check`,
`batch_event`, `line_status`) to Azure Event Hub, matching
`fabric/ontology/ontology_config.json` field-for-field. Not (yet) a
patch to `sources/fabric-data-generation` — it's an independent
producer that any Fabric Eventstream can consume. Also generates the
Supply Chain/ERP seed data (`run_business_seed.py`) — batch, not
streamed, per the medallion-layers memo's two-plane design.

## `fabric/` — the data platform

- [`fabric/ontology/`](fabric/ontology) — chocolate scenario:
  `ontology_config.json` (all 3 agent domains), dimension
  `tables/*.csv`, `farm-preparation.md`. Deployed as a real **Fabric IQ
  Ontology** item (preview), bound across the live Eventhouse and a
  Lakehouse (holding Factory/Quality's dimension tables and all 9
  Supply Chain/ERP tables, mirrored from the Fabric SQL Database since
  the Ontology has no direct SQL Database binding) — see
  `fabric/ontology/README.md`'s "Deploying to Fabric IQ" section.
- [`fabric/eventhouse/`](fabric/eventhouse) — Bronze/Silver/Gold KQL for
  the Eventhouse: raw ingestion tables, per-stage Silver pivots +
  dimension joins, and Gold rollups (one materialized view, three
  functions — see the medallion-layers design memo for why), plus
  `eventstream.json` routing the generator's four `RecordType`s into the
  four Bronze tables. Deployed and verified end to end against a live
  Eventhouse — see `fabric/eventhouse/README.md`'s "Verified against a
  live tenant" section for the issues that surfaced only from a real run
  and how they were fixed.
- [`fabric/sql-database/`](fabric/sql-database) — Supply Chain/ERP
  tables (9 total) + 3 Gold views on a Fabric SQL Database — the
  batch/transactional plane. Deployed and verified end to end against a
  real tenant — see `fabric/sql-database/README.md`.

## `foundry/` — the agentic layer

- [`foundry/kb/`](foundry/kb) — unstructured knowledge-base documents
  for Foundry IQ: process glossary, sensor metric ranges, business-rule
  definitions per domain. Uploaded to OneLake and indexed into a
  complete, queryable Foundry IQ knowledge base — data source, index,
  indexer, knowledge source, and knowledge base itself all provisioned
  by `terraform apply` — deployed and verified end to end — see
  `foundry/kb/README.md`. The one remaining manual step is
  project-level (adding the Search service as a Connected resource on
  the Foundry project) — see `foundry/kb/foundry-iq-setup.md`.
- `foundry/agents/` — not started. Coordinator + domain-specialist
  (Factory/Quality, Supply Chain, ERP/Orders) instructions and
  orchestration config for Microsoft Foundry.

## Cross-cutting

- [`infra/`](infra) — one Terraform state for Azure (Event Hub, Azure AI
  Search) and Fabric (capacity, workspace, Eventhouse, KQL Database,
  Connection, Eventstream, dimension Lakehouse, Fabric SQL Database,
  Fabric IQ Ontology). Provisions a real Azure Fabric capacity (F2 by
  default) rather than assuming one exists — spans both `fabric/` and
  `foundry/`, so it stays top-level rather than nested in either.
  Deployed and verified against a real tenant — see `infra/README.md`.
- [`doc/`](doc) — design memos as repo-tracked markdown, spanning the
  whole scenario rather than one layer: `cacao-data-model.md`
  (factories, process, entity model) and `cacao-medallion-layers.md`
  (Bronze/Silver/Gold design + the Kusto-docs reality check).

Nine production stages in three phases (Farm Preparation / Factory
Processing / Finishing) — Farm Preparation happens off-site near cocoa
origin, so only the six in-factory stages are modelled as
`production_stage` rows; see `fabric/ontology/farm-preparation.md`. Four
factories (EMEA-BCN Barcelona, NA-CHI Chicago, LATAM-GRU São Paulo,
APAC-SIN Singapore), 2-3 production lines each.

See [`doc/cacao-data-model.md`](doc/cacao-data-model.md) and
[`doc/cacao-medallion-layers.md`](doc/cacao-medallion-layers.md) for the
full entity model and rationale.
