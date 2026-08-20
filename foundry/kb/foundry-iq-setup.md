# Manual step: creating the Foundry IQ knowledge base

Everything up through a **knowledge source** — a queryable, semantically
-configured Azure AI Search index over `foundry/kb/*.md`, wrapped in a
`searchIndex`-kind knowledge source object — is automated by
`terraform apply` (see [`README.md`](README.md) and `infra/README.md`'s
"Foundry IQ knowledge base" section). This doc used to describe a
different, incorrect flow (picking "Microsoft OneLake" as a knowledge
*type* directly in a knowledge-base wizard) — that's not how the current
Foundry IQ model works. Correction, found the hard way (see "What went
wrong first" below): a **knowledge source** and a **knowledge base** are
two distinct objects, and the portal's knowledge-base picker only lists
existing knowledge sources — it does not let you point a knowledge base
at an arbitrary Search index/OneLake path directly.

Layering the actual **Foundry IQ knowledge base** object on top of the
knowledge source is the one piece with no documented Terraform/CLI/REST
path as of this writing — portal-only. This doc is that manual step,
done once per environment.

## What went wrong first

Trying to create a knowledge base in the Foundry portal produced:

> No supported knowledge sources available. Create one first.

...even though the Search index was already populated (verified via
direct `curl` queries returning real documents). Root cause: a
populated, queryable Search index is not by itself a "knowledge
source" — that's a separate object (`PUT .../knowledgesources/{name}`)
that has to explicitly wrap the index, and the portal's knowledge-base
creation picker only offers objects from `GET .../knowledgesources`, not
the raw index list. Two things were missing, both now fixed and
automated in `deploy_search_indexer.py`:

1. **A semantic configuration on the index itself** — Foundry IQ filters
   out indexes with no `semantic.configurations[]` block as ineligible.
2. **A `searchIndex`-kind knowledge source object**
   (`chocolate-factory-kb-source`) wrapping the index, with
   `searchIndexParameters.semanticConfigurationName` pointing at that
   configuration by name.

Both are GA (`api-version=2026-04-01`, no preview needed) and now run
automatically on every `terraform apply` — see
[Microsoft Learn's Knowledge Source overview](https://learn.microsoft.com/en-us/azure/search/agentic-knowledge-source-overview)
and [Create a Search Index Knowledge Source](https://learn.microsoft.com/en-us/azure/search/agentic-knowledge-source-how-to-search-index)
for the underlying model. Verify it's live before touching the portal:

```bash
az search admin-key show --resource-group <rg> --service-name <AZURE_SEARCH_SERVICE_NAME> --query primaryKey -o tsv
curl "https://<AZURE_SEARCH_SERVICE_NAME>.search.windows.net/knowledgesources?api-version=2026-04-01" -H "api-key: <key>"
```

Should return one entry, `chocolate-factory-kb-source`, kind
`searchIndex`. If it doesn't, re-run `terraform apply` in `infra/`
before going anywhere near the Foundry portal.

## Prerequisites

- **A Foundry project** — specifically a *project-based* Foundry
  resource, not a hub-based one. If you don't have a Foundry project
  yet, create one first in the [Microsoft Foundry portal](https://ai.azure.com/)
  — this is a separate Azure resource from anything `infra` provisions,
  and isn't part of this repo's Terraform state.
- **The Azure AI Search service added as a Connected resource** on that
  Foundry project — Management Center → Connected resources → New
  connection → Azure AI Search → select the service (`AZURE_SEARCH_SERVICE_NAME`
  below). Without this, the Foundry portal has no way to discover the
  knowledge source at all, regardless of whether it exists.
- **RBAC**, on top of whatever role got you access to the Foundry
  project itself: **Search Index Data Reader** (or Contributor) on the
  Azure AI Search service, so the Foundry project can query the index.

## Values you'll need

Pull these from `infra` (`terraform output`, run from `infra/`):

| Value | Terraform output | Where it's used |
|---|---|---|
| Search service name | `AZURE_SEARCH_SERVICE_NAME` | Connected resource + knowledge-base picker |
| Knowledge source name | (fixed) `chocolate-factory-kb-source` | Selecting the knowledge source in the wizard |

## Steps

1. Open the [Microsoft Foundry portal](https://ai.azure.com/) and
   switch into your Foundry project.
2. Confirm the Connected resource from "Prerequisites" above is in
   place (Management Center → Connected resources).
3. In the left nav, go to **Build → Knowledge**.
4. Click **Create a knowledge base**.
5. In the knowledge-source picker, select **`chocolate-factory-kb-source`**
   — it should now appear (this is the object that previously produced
   "No supported knowledge sources available").
6. Name the knowledge base something identifiable (e.g.
   `chocolate-factory-kb`), click **Create**, then **Save** once
   provisioned.

You'll need the knowledge base's name later when wiring it into a
Foundry project connection in Phase 3
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
underlying Search index actually has the expected content:

```bash
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
