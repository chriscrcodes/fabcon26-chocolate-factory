# agents

Phase 3 of the FabCon demo: the "agent spine" on top of the already-built
data spine (Fabric IQ Ontology, Fabric SQL Database, Eventhouse, Foundry
IQ knowledge base).

## What's here

- [`deploy_foundry_agent.py`](deploy_foundry_agent.py) creates/updates
  the actual Foundry Agent Service agent (`chocolate-factory-agent`)
  that ties the whole agent spine together — see "The Foundry agent"
  below.

The Fabric-side agent items this agent's tools are grounded in
(Data Agent, Operations Agent) live under
[`fabric/data-agent/`](../../fabric/data-agent) and
[`fabric/operations-agent/`](../../fabric/operations-agent) instead —
they're Fabric items, not Foundry ones.

## The Foundry agent

**Done, verified live across all three domains.** `chocolate-factory-agent`
(in the `chocolate-factory` Foundry project) is wired to 3 tools, all
generic `type: "mcp"` referencing a project connection
(`infra/azure.tf`) rather than a bare `server_url` + static bearer
header:

- **`knowledge_base`** — connection `chocolate-factory-kb`, category
  `RemoteTool`, authType `ProjectManagedIdentity`. Answers
  policy/definition questions (e.g. "What counts as an overdue
  invoice?") with citations.
- **`fabric_data_agent`** — connection `fabric-data-agent`, category
  `RemoteTool`, authType `UserEntraToken`. Answers Factory/Quality
  telemetry aggregates (e.g. "How many quality checks failed today?").
- **`fabric_iq_ontology`** — connection `fabric-iq-ontology`, category
  `RemoteTool`, authType `UserEntraToken`. Answers structured
  relationship questions across all 22 entity types (e.g. "What entity
  types exist in the ontology?").

The two Fabric tools needed `UserEntraToken` (forwards the calling
user's own signed-in identity) specifically because both Fabric
surfaces reject non-interactive identities for actual query execution
-- confirmed live, not assumed:

- **`ProjectManagedIdentity` (same pattern as the knowledge base)
  doesn't work for either Fabric tool.** The connection itself
  authenticates fine, but the Fabric Data Agent returns an internal
  "technical error" from its own answer synthesis for any
  non-interactive identity -- reproduced with both the project's own
  managed identity and an unrelated app-only service-principal token,
  ruling out a config-specific cause. The Ontology MCP endpoint has no
  application-only auth path at all, per
  [Microsoft's docs](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/fabric-iq):
  delegated auth is the only option.
- **The Foundry portal's own "Microsoft Fabric" connection wizard**
  (Settings → Connections → New connection → **Microsoft Fabric**,
  under Agent Knowledge Tools -- not literally named "Fabric IQ" as an
  older doc revision suggested) is currently broken: its "Custom Keys"
  form (`workspace-id` + `artifact-id` fields) fails live with a bare
  `400`. Reproduced directly against the connections API: `category:
  "MicrosoftFabric"` only accepts `authType: "AAD"` or
  `"UserEntraToken"` -- not `"CustomKeys"` as the form's own label
  implies -- and `workspace-id`/`artifact-id` turn out to be plain
  connection `metadata`, not `credentials.keys` (`AAD` authType takes
  no credentials block at all). Building that corrected shape
  (`category: "MicrosoftFabric"`, `authType: "AAD"`) creates cleanly
  and validates, but every agent query against it failed with
  `"Connection resolution failed"` -- this category+authType
  combination doesn't actually resolve to a usable identity at
  runtime, at least not as of this writing.
- **What actually works**: `category: "RemoteTool"` + `authType:
  "UserEntraToken"` + `audience:
  "https://analysis.windows.net/powerbi/api"` -- the same pattern
  Microsoft's docs use for their one concrete non-Ontology example (a
  Data Agent behind a workspace private link), applied here without
  the private-link angle. Confirmed live end to end: both tools return
  real, correct answers, matching what a direct user-token MCP client
  gets (see `fabric/data-agent/README.md`'s "Testing it yourself" for
  that client).

Querying the agent (once deployed):

```bash
TOKEN=$(az account get-access-token --scope https://ai.azure.com/.default --query accessToken -o tsv)
curl -s "https://aif-cacao-07499f0c.services.ai.azure.com/api/projects/chocolate-factory/openai/v1/responses" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"agent_reference": {"type": "agent_reference", "name": "chocolate-factory-agent"}, "input": [{"role": "user", "content": "How many quality checks failed today?"}]}'
```

Note the auth token scope for calling the agent (`https://ai.azure.com/.default`)
is different from the one Fabric MCP clients use directly
(`https://api.fabric.microsoft.com/.default`) -- the agent's own
`UserEntraToken` connections handle the Fabric-side token exchange
internally, using whichever identity called the agent in the first
place.
