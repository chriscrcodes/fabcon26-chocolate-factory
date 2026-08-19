# Manual step: creating the Foundry IQ knowledge base

Everything up through a queryable Azure AI Search index over
`foundry/kb/*.md` is automated by `terraform apply` (see
[`README.md`](README.md) and `infra/README.md`'s "Foundry IQ
knowledge base" section). Layering the actual **Foundry IQ knowledge
base** object on top of that index is the one piece with no documented
Terraform/CLI/REST path as of this writing — Microsoft Learn's own
walkthrough
([`azure/foundry/agents/how-to/foundry-iq-connect`](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/foundry-iq-connect))
only documents the *agent-attachment* step (Phase 3, once specialist
agents exist) via SDK/REST — the knowledge-base-creation step itself is
portal-only. This doc is that manual step, done once per environment.

## Prerequisites

- **A Foundry project** — specifically a *project-based* Foundry
  resource, not a hub-based one. Hub-based projects are explicitly
  unsupported for OneLake knowledge sources. If you don't have a
  Foundry project yet, create one first in the
  [Microsoft Foundry portal](https://ai.azure.com/) — this is a
  separate Azure resource from anything `infra` provisions, and
  isn't part of this repo's Terraform state.
- **RBAC**, on top of whatever role got you access to the Foundry
  project itself: **Search Index Data Reader** (or Contributor) on the
  Azure AI Search service, so the Foundry project can query the index
  it's about to be pointed at.
- The Search service and the Fabric workspace must be **in the same
  tenant** — true here since both are provisioned against the same
  tenant by `infra`.

## Values you'll need

Pull these from `infra` (`terraform output`, run from
`infra/`):

| Value | Terraform output | Where it's used in the wizard |
|---|---|---|
| Search service name | `AZURE_SEARCH_SERVICE_NAME` | "Select an existing Azure AI Search resource" |
| Fabric workspace ID | `FABRIC_WORKSPACE_ID` | "Workspace ID" field |
| Fabric lakehouse ID | `FABRIC_LAKEHOUSE_ID` | "Lakehouse ID" field |

The workspace/lakehouse IDs are GUIDs, not display names — the wizard
expects the same IDs `deploy_kb_files.py`/`deploy_search_indexer.py`
already used, not `"Chocolate Factory"` or
`"chocolate_factory_dimensions"`.

## Steps

1. Open the [Microsoft Foundry portal](https://ai.azure.com/) and
   switch into your Foundry project (top toggle/switcher — the docs
   refer to this as "the new Foundry UI").
2. In the left nav, go to **Build → Knowledge**.
3. Select **your Azure AI Search resource** (the value from
   `AZURE_SEARCH_SERVICE_NAME` above) as the resource this knowledge
   base will live on.
4. Click **Create a knowledge base**.
5. For knowledge type, choose **Microsoft OneLake**.
6. Click **Connect**, then supply:
   - **Workspace ID**: `FABRIC_WORKSPACE_ID`
   - **Lakehouse ID**: `FABRIC_LAKEHOUSE_ID`
7. Click **Create**, then **Save** the knowledge base once it's
   provisioned.

Name the knowledge base something identifiable (e.g.
`chocolate-factory-kb`) — you'll need to reference it by name when
wiring it into a Foundry project connection in Phase 3
(`{search_endpoint}/knowledgebases/{kb_name}/mcp?api-version=2026-05-01-preview`).

## Verifying it worked

Before wiring this to any agent, use the Foundry portal's own test
panel to ask the knowledge base a question that's grounded in exactly
one `foundry/kb/` doc, and confirm it answers correctly and cites that
doc — this isolates knowledge-base retrieval quality from any
agent/routing behavior that gets added on top later, same
verify-the-layer-below-before-building-on-it discipline used throughout
this repo's infra work. Good test questions, one per domain doc:

- *"What counts as an overdue invoice?"* → should ground in
  `03-erp-orders.md`.
- *"Why can't a nib shortage always be substituted the way a packaging
  shortage can?"* → should ground in `02-supply-chain.md`.
- *"What does CrystalFormIndex measure?"* → should ground in
  `01-factory-quality.md`.

If the answer comes back ungrounded or wrong, check first whether the
underlying Search index actually has the expected content — query it
directly:

```bash
az search admin-key show --resource-group <rg> --service-name <AZURE_SEARCH_SERVICE_NAME> --query primaryKey -o tsv
curl "https://<AZURE_SEARCH_SERVICE_NAME>.search.windows.net/indexes/chocolate-factory-kb/docs/\$count?api-version=2026-04-01" -H "api-key: <key>"
```

This should return `4` (one per `foundry/kb/00`–`03` doc). If it doesn't,
the problem is upstream of the knowledge base — re-run
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
