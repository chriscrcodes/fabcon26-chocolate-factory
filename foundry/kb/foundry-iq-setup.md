# Manual step: connecting the Foundry project to the knowledge base

The Foundry IQ knowledge base itself (`chocolate-factory-kb`) is fully
provisioned by `terraform apply` — see [`README.md`](README.md) and
`infra/README.md`'s "Foundry IQ knowledge base" section for how
`deploy_search_indexer.py` creates the data source, index, indexer,
knowledge source, and knowledge base end to end.

The one remaining manual step is project-level, not a Search-service
object, so it has no REST/Terraform equivalent here: the Foundry
project needs the Azure AI Search service registered as a **Connected
resource** before its portal can see or query the knowledge base.

## Prerequisites

- **A Foundry project** — specifically a *project-based* Foundry
  resource, not a hub-based one. If you don't have a Foundry project
  yet, create one first in the [Microsoft Foundry portal](https://ai.azure.com/)
  — this is a separate Azure resource from anything `infra` provisions,
  and isn't part of this repo's Terraform state.
- **RBAC**, on top of whatever role got you access to the Foundry
  project itself: **Search Index Data Reader** (or Contributor) on the
  Azure AI Search service, so the Foundry project can query the index.

## Values you'll need

Pull these from `infra` (`terraform output`, run from `infra/`):

| Value | Terraform output | Where it's used |
|---|---|---|
| Search service name | `AZURE_SEARCH_SERVICE_NAME` | Connected resource |

## Steps

1. Open the [Microsoft Foundry portal](https://ai.azure.com/) and
   switch into your Foundry project.
2. Management Center → Connected resources → New connection → Azure AI
   Search → select the service (`AZURE_SEARCH_SERVICE_NAME` above).
3. In the left nav, go to **Build → Knowledge**. The
   **`chocolate-factory-kb`** knowledge base should already be listed —
   no creation step needed, it was provisioned by `terraform apply`.

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

If the knowledge base doesn't appear in the portal at all, the
Connected resource step above is the most likely gap — confirm it in
Management Center before checking anything else.

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

Attaching this knowledge base to a specialist agent (Phase 3) is a
separate, automatable step once agents exist — see
`foundry/kb/README.md`'s "Deploying" section and the FabCon demo plan's
Phase 2.5 write-up for the MCP connection details
(`allowed_tools: ["knowledge_base_retrieve"]`).
