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

## Deploying (planned — not built yet)

`demo/ontology/README.md`'s DIY `fabric-ontology`-accelerator deploy
path (Azure AI Search indexing via `sources/fabric-ontology`) is no
longer part of this plan — that repo isn't vendored into this checkout,
and Foundry IQ turned out to support **native OneLake file ingestion
with no ETL** (a knowledge source can point directly at files in
OneLake). The plan is to upload these markdown files into a Lakehouse's
Files area (same mechanism as `demo/ontology/deploy_dimension_lakehouse.py`
uses for the dimension CSVs) and register that path as a Foundry IQ
knowledge source directly — no Azure AI Search indexing script needed.

Once the Coordinator + specialist agents exist (`demo/agents/`, not
started yet), each specialist should be scoped to its own document(s) —
`01`, `02`, `03` respectively — plus the shared `00` overview, rather
than every agent indexing all four.

## Extending

Keep new KB documents narrow and specific — glossary entries, thresholds,
"why" explanations, business rules — not restatements of what a table's
columns already say. A KB doc earns its place when it answers a question
the schema alone can't (e.g. "what counts as overdue," "what does
CrystalFormIndex actually measure").
