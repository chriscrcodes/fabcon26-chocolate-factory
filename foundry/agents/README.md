# agents

Phase 3 of the FabCon demo: the "agent spine" on top of the already-built
data spine (Fabric IQ Ontology, Fabric SQL Database, Eventhouse, Foundry
IQ knowledge base).

## What's here

- [`data-agent/`](data-agent) — definition parts for
  `fabric_data_agent.business` (`infra/fabric.tf`): `data_agent.json`,
  `draft/stage_config.json` (the AI instructions), and one
  `datasource.json` per grounded source. Schema verified against
  [Microsoft Learn's Data Agent item definition article](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/data-agent-definition).
- [`generate_data_agent_definition.py`](generate_data_agent_definition.py)
  generates the `elements` tree (which tables/columns are selected) —
  the API doesn't auto-discover a source's schema from just an
  artifactId; every table/column has to be listed explicitly with
  `is_selected: true`.
- [`deploy_foundry_agent.py`](deploy_foundry_agent.py) creates/updates
  the actual Foundry Agent Service agent (`chocolate-factory-agent`)
  that ties the whole agent spine together — see "The Foundry agent"
  below.

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
  gets (see "Testing it yourself" below for that client).

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

## Data Agent (Fabric item) status

**Eventhouse-only, done and verified live.** Bound to the Eventhouse's
`silver_quality_check` and `silver_line_status` tables via
`type: "kusto"`. Confirmed answering real questions correctly through
the Data Agent's own MCP endpoint and the Fabric portal's own chat
panel (see "Testing it yourself" below) — e.g. "How many quality
checks failed today?" → correct, grounded answer.

`silver_batch` is excluded (materialized view, not a plain table —
showed a permission/deleted warning in the portal even with an
identical `is_selected: true` config to the two working tables; same
category of limitation that already blocked OneLake mirroring for it
elsewhere in this repo).

**Supply Chain/ERP via a second (Lakehouse) source: abandoned, not a
bug on our end.** The plan was one shared data agent spanning both
engines rather than one per domain — Fabric SQL Database directly
(`type: "data_warehouse"`) was ruled out first (doesn't function
against a genuine `SQLDatabase`-type item; no `sql_database` value
exists in the `type` enum at all). Switched to the dimension
Lakehouse's mirror of the same 9 tables instead, and tried five
configurations live:

1. Flat top-level table list (the shape that works for Kusto)
2. Wrapped in a `lakehouse_tables` root element
3. Top-level `type: "lakehouse"` instead of `"lakehouse_tables"`
   (rejected outright — "Data source type is immutable")
4. SQL-analytics-endpoint type names (`varchar`/`float`/`int`) instead
   of Delta type names
5. An **exact byte-for-byte reproduction** of a config built by
   removing and re-adding the source through the Fabric portal's own
   "+ Add data source" picker — confirmed in the portal's own Sources
   view as connected, no warning, all 21 tables visible under
   `Schemas > dbo > Tables`

Every one of the five failed identically: the agent reports "the
available data sources do not contain supplier information" — via this
agent's MCP endpoint **and** the portal's own native chat panel, even
for configuration (5), which the portal itself had just generated and
displayed as healthy. That last result is what rules out a
configuration mistake on our end — if the portal's own generated
config fails in the portal's own chat, the gap is in Data Agent +
Lakehouse Tables query execution for this data shape, not in anything
authored here. Supply Chain/ERP grounding for the Foundry agent
comes from the **Fabric IQ Ontology** instead (already covers all 9
tables with 27 real relationships, verified working independently of
this issue).

## Testing it yourself

The Data Agent has no plain REST "ask a question" endpoint — querying
goes over MCP. Minimal Python client (needs `az login` first):

```python
import asyncio
import httpx
from azure.identity import AzureCliCredential
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

WORKSPACE_ID = "<FABRIC_WORKSPACE_ID>"
DATA_AGENT_ID = "<FABRIC_DATA_AGENT_ID>"
MCP_URL = f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{WORKSPACE_ID}/dataagents/{DATA_AGENT_ID}/agent"

async def main():
    credential = AzureCliCredential()
    token = credential.get_token("https://api.fabric.microsoft.com/.default").token
    http_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=120.0)
    async with streamable_http_client(MCP_URL, http_client=http_client) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tool = (await session.list_tools()).tools[0]
            result = await session.call_tool(tool.name, {"userQuestion": "How many quality checks failed today?"})
            for c in result.content:
                print(getattr(c, "text", c))

asyncio.run(main())
```

Requires the Data Agent to be **published** first (a draft-only agent
can't be queried at all):

```bash
curl -X POST "https://api.fabric.microsoft.com/v1/workspaces/<WORKSPACE_ID>/dataAgents/<DATA_AGENT_ID>/staging/publish" \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"description": "..."}'
```

`uv run --with azure-identity --with mcp --with httpx <script>.py` —
the `mcp` package's API has changed across versions (`streamablehttp_client`
→ `streamable_http_client`, `headers=` kwarg removed in favor of an
`httpx.AsyncClient`, `tool.inputSchema` → `tool.input_schema`); the
snippet above is the current shape as of this writing.
