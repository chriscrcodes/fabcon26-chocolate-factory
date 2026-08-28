# Connecting the Foundry project to the knowledge base

The Foundry IQ knowledge base itself (`chocolate-factory-kb`) is fully
provisioned by `terraform apply` — see the root [`README.md`](../../README.md)
and [`SETUP.md`](../../SETUP.md)'s "Foundry IQ knowledge base" section
for how `deploy_search_indexer.py` creates the data source, index,
indexer, knowledge source, and knowledge base end to end.

The Foundry project's **Connected resource** to that Search service,
and the project-level **connection** that exposes the knowledge base
as an MCP tool an agent can call, are also both provisioned by
`terraform apply` — `azurerm_role_assignment.foundry_project_search_reader`
and `azapi_resource.foundry_iq_kb_connection` in `infra/azure.tf`. See
that file and [`SETUP.md`](../../SETUP.md)'s "Wiring the knowledge base
into the Foundry project as an agent tool" section for why this needed
`azapi_resource` rather than a native `azurerm` resource. Nothing here
requires a manual portal step anymore.

## Prerequisite: the Foundry project itself

**A Foundry project** — specifically a *project-based* Foundry
resource, not a hub-based one — must exist before this can apply.
`infra/azure.tf` provisions one (`azurerm_cognitive_account` +
`azurerm_cognitive_account_project`), so running `terraform apply`
from `infra/` creates it if it doesn't already exist; no separate
manual creation step is needed.

## Verifying it worked

Use the Foundry portal's own test panel to ask the knowledge base a
question that's grounded in exactly one `foundry/kb/` doc, and confirm
it answers correctly and cites that doc — this isolates knowledge-base
retrieval quality from any agent/routing behavior that gets added on
top later, same verify-the-layer-below-before-building-on-it discipline
used throughout this repo's infra work. Good test questions, one per
domain doc:

- *"What counts as an overdue invoice?"* → should ground in
  `03-erp-orders.md`.
- *"Why can't a nib shortage always be substituted the way a packaging
  shortage can?"* → should ground in `02-supply-chain.md`.
- *"What does CrystalFormIndex measure?"* → should ground in
  `01-factory-quality.md`.

If the knowledge base doesn't appear in the portal at all, check
Management Center → Connected resources for the Azure AI Search
service (`AZURE_SEARCH_SERVICE_NAME` from `terraform output`) before
checking anything else.

If it appears but answers come back ungrounded or wrong, check whether
the underlying Search index actually has the expected content:

```bash
az search admin-key show --resource-group <rg> --service-name <AZURE_SEARCH_SERVICE_NAME> --query primaryKey -o tsv
curl "https://<AZURE_SEARCH_SERVICE_NAME>.search.windows.net/indexes/chocolate-factory-kb/docs/\$count?api-version=2026-04-01" -H "api-key: <key>"
```

This should return `4` (one per `foundry/kb/00`–`03` doc). If it
doesn't, the problem is upstream of the knowledge base — re-run
`terraform apply` in `infra`, or manually re-run the indexer
(`POST .../indexers/chocolate-factory-kb-indexer/run`) and check its
status (`GET .../indexers/chocolate-factory-kb-indexer/status`) before
troubleshooting the Foundry-side knowledge base at all.

## What this doesn't cover

Attaching this knowledge base's connection
(`AZURE_FOUNDRY_KB_CONNECTION_NAME` output, `chocolate-factory-kb`) to
a specific agent's tool list is a separate, per-agent step once an
agent exists — see `SETUP.md`'s foundry/kb "Deploying" section and the
FabCon demo plan's Phase 2.5 write-up for the MCP connection details
(`allowed_tools: ["knowledge_base_retrieve"]`).
