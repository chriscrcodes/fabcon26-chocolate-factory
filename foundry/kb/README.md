# kb

Unstructured grounding documents for the chocolate factory agents —
process explanations, glossaries, and business rules that don't belong
in structured tables but that an agent needs to answer well. Structured
data lives in `fabric/ontology` (schema) and `fabric/eventhouse` (Bronze/
Silver/Gold); this folder is the knowledge-base/RAG side, for Foundry IQ.

| File | Grounds |
|---|---|
| [`00-company-overview.md`](00-company-overview.md) | Coordinator + all specialists — factories, the 9-stage process, recipes, domain boundaries |
| [`01-factory-quality.md`](01-factory-quality.md) | Factory/Quality specialist — sensor metric ranges and what they mean, quality-check/OEE definitions |
| [`02-supply-chain.md`](02-supply-chain.md) | Supply Chain specialist — materials, supplier rating, inventory/reorder logic |
| [`03-erp-orders.md`](03-erp-orders.md) | ERP/Orders specialist — products, customer segments, order/invoice lifecycle |

Note the version markers inside `02-supply-chain.md` and
`03-erp-orders.md`: those two domains have an ontology schema
(`fabric/ontology/ontology_config.json`) but no data generator yet, so
their KB docs define the intended business semantics ahead of that
build rather than describing live data.

## Deploying

Correction to an earlier assumption in this file: Foundry IQ's OneLake
ingestion is **not** ETL-free in the sense of "no Azure AI Search
involved" — it still provisions and runs a real Search index, just
without you hand-building the ingestion/chunking pipeline. Confirmed
against
[Microsoft Learn](https://learn.microsoft.com/en-us/fabric/onelake/onelake-foundry-knowledge)
and
[the OneLake-files-indexer how-to](https://learn.microsoft.com/en-us/azure/search/search-how-to-index-onelake-files).

Deployed and verified live, in two parts:

1. **Search-side plumbing, including the knowledge source — fully
   automated, done.** [`deploy_kb_files.py`](deploy_kb_files.py)
   uploads `00`–`03` to the dimension Lakehouse's `Files/kb/` folder
   (same OneLake mechanism `fabric/ontology/deploy_dimension_lakehouse.py`
   uses for the dimension CSVs, just without a "Load Table" step —
   these are documents, not tabular data).
   [`deploy_search_indexer.py`](deploy_search_indexer.py) configures an
   Azure AI Search OneLake files data source, index (with a semantic
   configuration), indexer, and a `searchIndex`-kind **knowledge
   source** object wrapping the index. All wired into `infra/fabric.tf`
   as `null_resource`s and run on `terraform apply` — see
   `infra/README.md`'s "Foundry IQ knowledge base" section for the
   live-verified bugs found (workspace role must be Contributor, not
   Viewer; document keys need a `base64Encode` field mapping; a
   populated index alone isn't enough — the Foundry portal only
   recognizes a knowledge *source* object, which needs a semantic
   configuration to be considered eligible). Confirmed live: `4/4` docs
   indexed, a test query for "overdue invoice" correctly surfaces
   `03-erp-orders.md`, and `GET .../knowledgesources` returns the
   wrapping object.
2. **The Foundry IQ knowledge base itself — manual, one-time, not yet
   done.** Layering a Foundry IQ knowledge base on top of the knowledge
   source above has no documented Terraform/CLI/REST path as of this
   writing — only a Foundry-portal wizard, and it also requires the
   Search service to be added as a Connected resource on the Foundry
   project first. See [`foundry-iq-setup.md`](foundry-iq-setup.md) for
   the exact prerequisites, values, click-through steps, and how to
   verify it worked — including the "No supported knowledge sources
   available" error this produces if the knowledge source/semantic
   config isn't in place yet.

Once the Coordinator + specialist agents exist (`foundry/agents/`, not
started yet), each specialist should be scoped to its own document(s) —
`01`, `02`, `03` respectively — plus the shared `00` overview, via MCP
(`allowed_tools: ["knowledge_base_retrieve"]` on a Foundry project
connection to the knowledge base's MCP endpoint) rather than every
agent indexing all four.

## Extending

Keep new KB documents narrow and specific — glossary entries, thresholds,
"why" explanations, business rules — not restatements of what a table's
columns already say. A KB doc earns its place when it answers a question
the schema alone can't (e.g. "what counts as overdue," "what does
CrystalFormIndex actually measure").
