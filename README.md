# 🍫 Chocolate Factory — Fabric + Foundry in Action

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![IaC: Terraform](https://img.shields.io/badge/IaC-Terraform-844FBA?logo=terraform&logoColor=white)](infra)
[![Microsoft Fabric](https://img.shields.io/badge/Microsoft-Fabric-0078D4?logo=microsoft&logoColor=white)](fabric)
[![Microsoft Foundry](https://img.shields.io/badge/Microsoft-Foundry-0078D4?logo=microsoft&logoColor=white)](foundry)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](simulator)

A live, full-stack demo built for FabCon Europe 2026: four virtual
chocolate factories stream real production telemetry into Microsoft
Fabric, ground a real Fabric IQ Ontology and a Foundry IQ knowledge
base, and get answered live by a single Microsoft Foundry agent that
picks the right tool per question — and cites it.

```
        _____________________________
       |  ___   ___   ___   ___      |     ~ ~ ~ ~
       | |   | |   | |   | |   |     |    (  smoke  )
       | |___| |___| |___| |___|     |     ~ ~ ~ ~
       |    CHOCOLATE FACTORY   [||] |________
       |_________________________[||]|  o   o |
        |  |  |  |  |  |  |  |  |    |________|
    ____|__|__|__|__|__|__|__|__|_____
   /  cacao -> grind -> temper -> bar  \
  '--------------------------------------'
        |####|  |####|  |####|  |####|
        '----'  '----'  '----'  '----'
```

Nothing here is a mockup. Every box in the diagrams below is real,
deployed infrastructure, provisioned by one `terraform apply`.

**4** factories · **10** production lines · **23** ontology entities ·
**29** bound relationships · **3** agent tools · **1** `terraform apply`

## 🏭 What it is

- **4 factories** (Barcelona, Chicago, São Paulo, Singapore), each with
  2–3 production lines cycling through a 6-stage chocolate-making
  process, streaming live sensor/quality/batch telemetry
- **A full medallion architecture** on Fabric Real-Time Intelligence
  (Eventhouse Bronze → Silver → Gold)
- **A Fabric IQ Ontology** binding that telemetry together with Supply
  Chain/ERP data into one queryable graph — 23 entity types, 29 real
  bound relationships, including a `batch_material_usage` junction
  table answering "which supplier/material lots does this usage
  record cover" (verified live)
- **A Foundry IQ knowledge base** grounding policy/definition questions
  a schema alone can't answer
- **One Foundry Agent Service agent**, `chocolate-factory-agent`,
  reasoning across all three live and citing which tool it used —
  including chaining two tools together when a question needs it
- **A Fabric Operations Agent** watching the tempering stage for
  early defect signals

## 🗺️ Architecture

```mermaid
flowchart LR
    SIM["simulator"] -->|Event Hub| ES["Eventstream"]
    ES --> EH["Eventhouse\nBronze / Silver / Gold KQL"]
    SQL["Fabric SQL Database\nSupply Chain / ERP"] --> ONT
    EH -->|OneLake mirroring| ONT["Fabric IQ Ontology\n23 entities, 29 relationships"]
    KB["foundry/kb/*.md"] -->|Azure AI Search| FKB["Foundry IQ\nknowledge base"]
    EH -->|Fabric Data Agent| AGENT["chocolate-factory-agent\n(Foundry Agent Service)"]
    ONT --> AGENT
    FKB --> AGENT
    EH -->|Operations Agent| ALERT["Alert\n(Power Automate / Teams)"]
```

One `terraform apply` provisions every box above.

### Domain model: factories, the ontology, and the Fabric / Foundry split

```mermaid
flowchart TB
    subgraph FACTORIES["4 factories"]
        direction LR
        BCN["Barcelona\nEMEA-BCN"]
        CHI["Chicago\nNA-CHI"]
        GRU["São Paulo\nLATAM-GRU"]
        SIN["Singapore\nAPAC-SIN"]
    end

    subgraph FABRIC["Microsoft Fabric"]
        subgraph ONTOLOGY["Fabric IQ Ontology -- 23 entities, 29 relationships"]
            direction LR
            FQ["Factory & Quality\nfactory, line, batch,\nsensor_reading, quality_check"]
            SC["Supply Chain\nsupplier, material,\ninventory, shipment"]
            ERP["ERP & Orders\ncustomer, product,\nsales_order, invoice"]
            SC -->|shipment_to_factory| FQ
            ERP -->|product_to_recipe| FQ
        end
    end

    subgraph FOUNDRY["Microsoft Foundry"]
        AGENT["chocolate-factory-agent"]
    end

    FACTORIES -->|live telemetry| FQ
    ONTOLOGY -->|fabric_iq_ontology tool| AGENT
    FQ -->|fabric_data_agent tool| AGENT
```

Supply Chain and ERP only ever connect to Factory & Quality, never to
each other directly — a real gap in the data model, not a diagram
simplification (see "Known limitations" below).

## 📂 Repository map

| Folder | What's there |
|---|---|
| [`SETUP.md`](SETUP.md) | Everything needed to deploy and run this end to end — prerequisites, `terraform apply`, the simulator, per-subsystem deploy/troubleshooting detail, the demo query walkthrough, and the full verified question bank |
| [`CHOCOLATE-FACTORY.md`](CHOCOLATE-FACTORY.md) | The chocolate manufacturing process this demo simulates — the four factories and the bean-to-bar stages, with photos |
| [`simulator/`](simulator) | Python telemetry generator streaming production-line events to Event Hub |
| [`fabric/`](fabric) | The Fabric side: Eventhouse KQL, the Ontology, the SQL Database, the Data Agent, and the Operations Agent |
| [`foundry/`](foundry) | The Foundry side: the agent itself (`foundry/agents/`) and the Foundry IQ knowledge base (`foundry/kb/`) |
| [`infra/`](infra) | One Terraform state provisioning everything above, Azure and Fabric together |

## 🧬 Data model

One Coordinator agent reasoning across three domains, each owning its
own tables — `factory`, `batch`, and `recipe` are the shared keys that
let answers from different domains join together:

| Domain | Owns | Notes |
|---|---|---|
| **Factory / Quality** | `factory`, `production_line`, `production_stage`, `batch`, `sensor_reading`, `quality_check`, `line_status` | Streamed telemetry — see [`CHOCOLATE-FACTORY.md`](CHOCOLATE-FACTORY.md) for the process it represents |
| **Supply Chain** | `supplier`, `material`, `inventory`, `batch_material_usage`, `shipment` | Batch-loaded, Fabric SQL Database |
| **ERP / Orders** | `customer`, `product`, `sales_order`, `order_line`, `invoice` | Batch-loaded, Fabric SQL Database |

Cross-domain relationships (all real, bound in the Fabric IQ Ontology
— see [`SETUP.md`'s `fabric/ontology` section](SETUP.md#fabricontology)
for which ones have live instance data vs. type-only):

```
production_line.FactoryId   -> factory.FactoryId
batch.LineId                -> production_line.LineId
batch.RecipeId               -> recipe.RecipeId
sensor_reading.LineId        -> production_line.LineId
sensor_reading.BatchId       -> batch.BatchId
quality_check.BatchId        -> batch.BatchId
line_status.LineId           -> production_line.LineId
material.SupplierId          -> supplier.SupplierId
batch_material_usage.BatchId    -> batch.BatchId
batch_material_usage.MaterialId -> material.MaterialId
inventory.MaterialId         -> material.MaterialId
inventory.FactoryId          -> factory.FactoryId
shipment.FromFactoryId       -> factory.FactoryId
shipment.BatchId              -> batch.BatchId
product.RecipeId             -> recipe.RecipeId
sales_order.CustomerId       -> customer.CustomerId
order_line.OrderId            -> sales_order.OrderId
order_line.ProductId          -> product.ProductId
invoice.OrderId               -> sales_order.OrderId
```

Full column-level schema: `fabric/ontology/ontology_config.json`.

## ⚡ Quick start

One `terraform apply` provisions the whole stack — Fabric capacity,
workspace, Eventhouse, Ontology, SQL Database, the Foundry IQ
knowledge base, the Foundry project, and the agent itself, wired to
all its tools. Full deploy prerequisites, the simulator (real-time,
backfill, and per-scenario commands), and the query walkthrough are
all in **[`SETUP.md`](SETUP.md)**.

## 🎤 The demo

Built for "Multi-Agent Data Systems: Fabric + Foundry in Action" (60
min, level 300). Four learning objectives, and where each lives in
the stack:

| Objective | Where it lives |
|---|---|
| Fabric grounding enterprise data | Eventhouse (live telemetry) + Fabric SQL Database (Supply Chain/ERP) + Foundry IQ knowledge base, all queried live, not mocked |
| Fabric IQ for context-aware reasoning | The Fabric IQ Ontology — 23 entity types, 29 real bound relationships spanning both engines |
| Orchestrate AI workflows using Foundry | One Foundry Agent Service agent, 3 tools, picking the right one per question and citing it |
| Multi-agent architectures combining data and AI agents | See "What we tried and didn't make the cut" below — this is where the talk earns its "in Action" credibility rather than a polished happy path |

Short version: the agent picks the right tool per question and cites
it. The exact commands to run it, and the full verified question
bank, are in [`SETUP.md`](SETUP.md#6-query-the-demo-agent).

- *"What counts as an overdue invoice?"* → the knowledge base, with a
  citation
- *"Are there any anomalies I should be aware of?"* → live telemetry
  via the Fabric Data Agent
- *"Which factories are receiving shipments, and how many quality
  checks failed today at those same factories?"* → the agent chains
  the Ontology and the Data Agent together, one feeding the other

### What we tried and didn't make the cut

The section that makes this a level-300 talk instead of a vendor
demo. Both are presented as evidence of rigor, not confessions — the
audience should leave trusting the parts that *do* work more, not
less.

**A second Data Agent source for Supply Chain/ERP — abandoned.**
Tried 5 configurations grounding the shared Fabric Data Agent in a
Lakehouse mirror of the Supply Chain/ERP tables, escalating each
time, including an exact byte-for-byte reproduction of a config the
Fabric portal's own "+ Add data source" picker generated. All 5
failed identically — the decisive one was tested in **the portal's
own native chat panel**, not just this repo's code, which is what
rules out a config mistake and confirms a genuine platform limitation
in Data Agent + Lakehouse Tables query execution for this data shape.
Resolution: Supply Chain/ERP grounding comes from the Ontology tool
instead — no functionality lost, just routed differently. Full
sequence in [`SETUP.md`'s `fabric/data-agent` section](SETUP.md#fabricdata-agent).

**A2A agent-to-agent orchestration — abandoned, with a fully-diagnosed
root cause.** A timeboxed spike to build a real Coordinator + 2
specialist-agent architecture, specifically to serve the "multi-agent
architectures" objective more literally than one agent with three
tools does. Two genuinely undocumented requirements were found and
fixed along the way (a separate "Foundry Agent Consumer" role grant
for the calling agent's own instance identity, and `kind`
discriminators the A2A JSON-RPC schema requires but Microsoft's own
published examples omit). The raw A2A protocol itself was gotten
working end to end — a direct JSON-RPC call to the target agent's
endpoint returned the correct, verified answer. But the actual
product mechanism for wiring this into a live agent (the
`a2a_preview` tool) failed reproducibly with an opaque,
non-diagnosable error, confirmed not to be a config mistake by
standing up Application Insights specifically to chase it.

The honest framing: "multi-agent" in production today most often
means one orchestrating agent reasoning across multiple *specialized
data agents and knowledge sources* — not necessarily multiple
*conversational* agents talking to each other. The centerpiece
question above is real evidence that pattern works well right now.
This demo also tried the more literal interpretation via Foundry's
`a2a_preview` tool, and can show exactly where that stands today: the
protocol and auth model work, the product's own tool-invocation layer
for it doesn't yet.

### If asked live

Two things worth having an honest one-liner ready for, rather than being
caught off guard:

- **Scale is demo-scale, not enterprise-scale, on purpose.** 4
  factories, 10 lines, 9 suppliers — real enough to exercise every
  part of the architecture live, not a claim about production volume.
- **Fresh-turn pronoun references don't resolve** (*"who supplies
  **this** product?"*) — a known, acknowledged model behavior, not a
  bug in this demo specifically; ask with a concrete noun instead. See
  [`SETUP.md`'s question bank](SETUP.md#known-to-fail--useful-for-the-honesty-beat-not-the-main-script)
  for the exact failure.

## ✅ Verified, not asserted

Every question in the [full question bank](SETUP.md#the-full-verified-question-bank)
is tagged by what actually happened when it was run against the
live, deployed stack — not what should happen in theory:

| | Count | Meaning |
|---|---|---|
| ✅ Verified | 11 | Run live, exact result recorded — safe to use as-is |
| 🧪 Candidate | 8 | Plausible given the real data model, not yet run live |
| ❌ Known to fail | 4 | Run live and failed, with the diagnosed reason |

The "Known limitations" below come from that same discipline — real
failures, kept in the docs instead of quietly dropped.

## ⚠️ Known limitations

Stated plainly, not hidden:

- The Fabric Data Agent only grounds Factory/Quality telemetry — a
  second Lakehouse-backed source for Supply Chain/ERP is enum-blocked
  (no `sql_database` source type exists at all) and its Lakehouse-table
  alternative failed identically across 5 configurations, including an
  exact reproduction of what the Fabric portal's own picker generates
  — reproduced in the **portal's own native chat**, which rules out a
  config mistake on our end (see
  [`SETUP.md`'s `fabric/data-agent` section](SETUP.md#fabricdata-agent)).
  Confirmed dead end, not lack of effort; Supply Chain/ERP grounding
  comes from the Ontology tool instead, which already covers it fully.
- `fabric_data_agent` and `fabric_iq_ontology` can legitimately give
  different answers to an overlapping question (e.g. "how many quality
  checks failed today") — the Data Agent queries the live Eventhouse
  directly, while the Ontology reads a periodically-materialized copy
  (`materialize_static_sources.py`, refreshed on every `terraform
  apply`, not continuously). Architectural, not a bug — see
  [`SETUP.md`'s "A correctly-shaped definition is not enough"](SETUP.md#fabricontology)
  for the mechanism.
- The Operations Agent needs one manual portal step after `terraform
  apply` to connect its alert action — see
  [`SETUP.md`'s Operations Agent section](SETUP.md#7-one-time-manual-step-operations-agent).
- `batch_material_usage`'s traceability is real but partial, confirmed
  live: `usage_to_material` → `material_to_supplier` correctly answers
  "which material lot and supplier does this usage record cover" (a
  real `USE-####` ID returns the exact right material and supplier
  every time). `usage_to_batch` returns nothing, because its `BatchId`
  values are synthetic-but-plausible (same accepted convention
  `shipment.BatchId` already uses), generated in a separate seed run
  from the live-streamed `Batch` entity's own IDs — the two ID spaces
  never overlap by construction. So this answers "who supplies our
  materials, per usage record," not "which supplier fed *this specific
  live batch*."
- Two things stay outside Terraform's reach on a fresh deploy: Fabric
  IQ's region availability, and Fabric workspace access for anyone who
  isn't the person who ran `terraform apply` — see
  [`SETUP.md`'s "Reproducing on a different subscription"](SETUP.md#reproducing-on-a-different-subscription).

## 📜 License

MIT — see [`LICENSE`](LICENSE).
