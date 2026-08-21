# agents

Phase 3 of the FabCon demo: the "agent spine" on top of the already-built
data spine (Fabric IQ Ontology, Fabric SQL Database, Eventhouse, Foundry
IQ knowledge base). Per the agent-spine plan's cost/complexity
priority: one shared Fabric Data Agent (not one per domain) before any
Foundry Agent Service agent gets built on top.

## What's here

- [`data-agent/`](data-agent) — definition parts for
  `fabric_data_agent.business` (`infra/fabric.tf`): `data_agent.json`,
  `draft/stage_config.json` (the AI instructions), and one
  `datasource.json` per grounded source. Schema verified against
  [Microsoft Learn's Data Agent item definition article](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/data-agent-definition).
- [`generate_data_agent_definition.py`](generate_data_agent_definition.py)
  generates the `elements` tree (which tables/columns are selected) for
  each datasource — the API doesn't auto-discover a source's schema
  from just an artifactId; every table/column has to be listed
  explicitly with `is_selected: true`.

## Status

**Eventhouse source: done, verified live.** Bound to the Eventhouse's
`silver_batch`, `silver_quality_check`, `silver_line_status` tables via
`type: "kusto"`. Confirmed answering real questions correctly through
the Data Agent's own MCP endpoint (see "Testing it yourself" below) —
e.g. "How many quality checks failed today?" → correct, grounded
answer.

**Lakehouse source (Supply Chain/ERP mirror): not yet working.** Points
at the same dimension Lakehouse the Ontology mirrors these 9 tables
into (`fabric/ontology/deploy_dimension_lakehouse.py`) — not the Fabric
SQL Database directly, since `type: "data_warehouse"` was tried first
and live-verified not to function against a genuine `SQLDatabase`-type
Fabric item (no `sql_database` value exists in the datasource `type`
enum at all). Four schema variants were tried against the Lakehouse
mirror instead (flat vs. wrapped `elements`, `lakehouse` vs.
`lakehouse_tables` as the top-level `type`, Delta vs. SQL-analytics
column type names) — all deploy without any schema/validation error,
but none actually became queryable; the agent still describes the
domain conceptually (from the prose fields) but reports the real
tables as inaccessible whenever asked a real question. See
[`generate_data_agent_definition.py`](generate_data_agent_definition.py)'s
docstring for the exact sequence tried. Root cause unconfirmed —
possibly a permissions/consent step only visible in the Fabric
portal's own Data Agent UI, not exposed via REST.

**Next step if picking this back up**: open the Data Agent
(`chocolate_factory_data_agent`) in the Fabric portal directly and
check its Sources tab for the Lakehouse connection — portal UI may
surface a connection error REST doesn't. If the portal shows it as
genuinely connected there, the gap is specific to how definitions
authored via API/Terraform represent a Lakehouse source, not the
underlying connectivity.

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
