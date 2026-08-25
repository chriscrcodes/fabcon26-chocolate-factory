# data-agent

Definition parts for `fabric_data_agent.business` (`infra/fabric.tf`),
the Fabric item the Foundry agent's `fabric_data_agent` tool calls (see
`foundry/agents/README.md`).

## What's here

- [`data_agent.json.tmpl`](data_agent.json.tmpl),
  [`draft/stage_config.json.tmpl`](draft/stage_config.json.tmpl) (the
  AI instructions), and one `datasource.json.tmpl` per grounded source
  under [`draft/kusto-eventhouse/`](draft/kusto-eventhouse). Schema
  verified against
  [Microsoft Learn's Data Agent item definition article](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/data-agent-definition).
- [`generate_data_agent_definition.py`](generate_data_agent_definition.py)
  generates the `elements` tree (which tables/columns are selected) —
  the API doesn't auto-discover a source's schema from just an
  artifactId; every table/column has to be listed explicitly with
  `is_selected: true`.
- [`publish_data_agent.py`](publish_data_agent.py) publishes the Data
  Agent (`infra/fabric.tf`'s `null_resource.publish_data_agent`) — a
  draft-only agent can't be queried at all.

## Status

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
