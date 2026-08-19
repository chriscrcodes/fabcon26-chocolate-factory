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

Correction to an earlier assumption in this file: Foundry IQ's OneLake
ingestion is **not** ETL-free in the sense of "no Azure AI Search
involved" — it still provisions and runs a real Search index, just
without you hand-building the ingestion/chunking pipeline. Confirmed
against
[Microsoft Learn](https://learn.microsoft.com/en-us/fabric/onelake/onelake-foundry-knowledge)
and
[the OneLake-files-indexer how-to](https://learn.microsoft.com/en-us/azure/search/search-how-to-index-onelake-files).

Deployed and verified live, in two parts:

1. **Search-side plumbing — fully automated, done.**
   [`deploy_kb_files.py`](deploy_kb_files.py) uploads `00`–`03` to the
   dimension Lakehouse's `Files/kb/` folder (same OneLake mechanism
   `demo/ontology/deploy_dimension_lakehouse.py` uses for the dimension
   CSVs, just without a "Load Table" step — these are documents, not
   tabular data).
   [`deploy_search_indexer.py`](deploy_search_indexer.py) configures an
   Azure AI Search OneLake files data source, index, and indexer over
   that folder. Both are wired into `demo/infra/fabric.tf` as
   `null_resource`s and run on `terraform apply` — see
   `demo/infra/README.md`'s "Foundry IQ knowledge base" section for the
   two live-verified bugs found (workspace role must be Contributor,
   not Viewer; document keys need a `base64Encode` field mapping).
   Confirmed live: `4/4` docs indexed, and a test query for "overdue
   invoice" correctly surfaces `03-erp-orders.md`.
2. **The Foundry IQ knowledge base itself — manual, one-time, not yet
   done.** Layering a Foundry IQ knowledge base on top of the Search
   index above has no documented Terraform/CLI/REST path as of this
   writing — only a Foundry-portal wizard (Build → Knowledge → select
   the `azurerm_search_service.kb` resource → "Microsoft OneLake" type
   → supply the Fabric workspace/lakehouse IDs → Create). Do this by
   hand once, then verify by querying the knowledge base directly in
   the portal's test panel before wiring it to any agent.

Once the Coordinator + specialist agents exist (`demo/agents/`, not
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
