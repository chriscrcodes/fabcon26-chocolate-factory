# kb

Unstructured grounding documents for the chocolate factory agents —
process explanations, glossaries, and business rules that don't belong
in structured tables but that an agent needs to answer well. Structured
data lives in `demo/ontology` (schema) and `demo/eventhouse` (Bronze/
Silver/Gold); this folder is the knowledge-base/RAG side, for Foundry IQ.

| File | Grounds |
|---|---|
| [`00-company-overview.md`](00-company-overview.md) | Coordinator + all specialists — factories, the 9-stage process, recipes, domain boundaries |
| [`01-factory-quality.md`](01-factory-quality.md) | Factory/Quality specialist — sensor metric ranges and what they mean, quality-check/OEE definitions |
| [`02-supply-chain.md`](02-supply-chain.md) | Supply Chain specialist — materials, supplier rating, inventory/reorder logic |
| [`03-erp-orders.md`](03-erp-orders.md) | ERP/Orders specialist — products, customer segments, order/invoice lifecycle |

Note the version markers inside `02-supply-chain.md` and
`03-erp-orders.md`: those two domains have an ontology schema
(`demo/ontology/ontology_config.json`) but no data generator yet, so
their KB docs define the intended business semantics ahead of that
build rather than describing live data.

## Deploying

`fabric-ontology` already has exactly this mechanism: each scenario
folder under `data/scenarios/<name>/` has a `documents/` subfolder,
indexed into Azure AI Search by
`infra/scripts/post-provision/03_upload_to_search.py`, then wired into
the Foundry agent as a knowledge-base tool alongside the SQL data
source. To use these:

1. Copy this folder's contents into
   `sources/fabric-ontology/data/scenarios/chocolate/documents/`
   (alongside `demo/ontology/`'s deploy target — see
   `demo/ontology/README.md`).
2. Run the scenario's `01`–`04` post-provision scripts, which create the
   Fabric SQL DB data source, index these documents into Azure AI
   Search, and provision the Foundry agent with both tools attached.

Once the 4-agent Coordinator + specialist split exists (`demo/agents/`,
not started yet), each specialist should be scoped to its own document(s)
— `01`, `02`, `03` respectively — plus the shared `00` overview, rather
than every agent indexing all four.

## Extending

Keep new KB documents narrow and specific — glossary entries, thresholds,
"why" explanations, business rules — not restatements of what a table's
columns already say. A KB doc earns its place when it answers a question
the schema alone can't (e.g. "what counts as overdue," "what does
CrystalFormIndex actually measure").
