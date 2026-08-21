#!/usr/bin/env python3
"""Create (or update) the single Foundry Agent Service agent for the
FabCon demo's agent spine -- one generalist agent wired to all three
domains, per the plan's "leanest starting point" (see
../../.claude/plans -- Coordinator + specialists is a later decision,
made only if this single agent's answer quality needs it).

Direct Fabric/Foundry REST calls (AzureCliCredential), same convention
as deploy_fabric_iq_ontology.py and deploy_search_indexer.py -- the
Foundry Agent Service's exact `agents` API shape isn't in any
Terraform provider, and its own docs (foundry-iq-connect) call it via
raw `requests` too, not an SDK method.

All three tools use the generic `type: "mcp"` shape, each referencing
an already-created project connection (infra/azure.tf) rather than a
bare server_url + static bearer header -- Microsoft's own docs say
static secrets aren't accepted in MCP tool headers; routing through a
stored connection is the documented alternative.

- **`knowledge_base`** -- connection `chocolate-factory-kb`, category
  `RemoteTool`, authType `ProjectManagedIdentity` (service-to-service:
  the project's own identity, fine for a knowledge base). Foundry IQ
  knowledge base (Factory/Quality, Supply Chain, ERP domain docs),
  `allowed_tools` restricted to `knowledge_base_retrieve`. Required the
  Search service's authOptions switched from apiKeyOnly to aadOrApiKey
  (infra/azure.tf) -- RBAC role assignments alone don't matter if the
  service rejects AAD tokens outright.
- **`fabric_data_agent`/`fabric_iq_ontology`** -- connections
  `fabric-data-agent`/`fabric-iq-ontology`, category `RemoteTool`,
  authType **`UserEntraToken`** (forwards the calling user's own
  signed-in identity, not a service principal). Both Fabric tools
  genuinely need this: the Fabric Data Agent returns an internal
  "technical error" for ANY non-interactive identity (confirmed with
  both the project's managed identity and an independent app-only
  service-principal token -- both get a real MCP response, just an
  error from the Data Agent's own answer synthesis, not an
  auth/transport failure), and the Ontology MCP endpoint has NO
  application-only auth path at all per Microsoft's docs
  (https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/fabric-iq)
  -- delegated auth is the only option for it. `UserEntraToken` +
  `audience: "https://analysis.windows.net/powerbi/api"` is the one
  combination confirmed live to actually work end to end (real,
  correct answers back from both tools, matching what a direct
  user-token MCP client gets) -- see infra/azure.tf's
  `fabric_data_agent_mcp_connection`/`fabric_iq_ontology_mcp_connection`
  comments for the two dead ends ruled out first (`ProjectManagedIdentity`,
  and a `MicrosoftFabric`-category `AAD` connection reverse-engineered
  from the Foundry portal's own broken "Microsoft Fabric" wizard, which
  creates cleanly but fails every query with "Connection resolution
  failed"). Tool type stays the generic `mcp`, not `fabric_iq_preview`
  -- that type is for `MicrosoftFabric`-category connections, which
  aren't what's used here.

  Also note: an early version of the Ontology connection pointed at
  agent365.svc.cloud.microsoft (Agent 365/Copilot Studio
  infrastructure, gated to Frontier-program tenants) -- the wrong
  endpoint for a Foundry agent entirely. The native
  `api.fabric.microsoft.com/v1/mcp/.../ontologyEndpoint` endpoint used
  now is the correct one.

Usage:
    AZURE_FOUNDRY_ACCOUNT_NAME=... AZURE_FOUNDRY_PROJECT_NAME=... \\
    AZURE_FOUNDRY_MODEL_DEPLOYMENT_NAME=... AZURE_SEARCH_SERVICE_NAME=... \\
        uv run --with azure-identity --with requests \\
        deploy_foundry_agent.py
"""

import json
import os

import requests
from azure.identity import AzureCliCredential

AGENT_NAME = "chocolate-factory-agent"
API_VERSION = "v1"


def get_token() -> str:
    return AzureCliCredential().get_token("https://ai.azure.com/.default").token


def agent_exists(session: requests.Session, project_endpoint: str) -> bool:
    resp = session.get(f"{project_endpoint}/agents", params={"api-version": API_VERSION})
    resp.raise_for_status()
    return any(agent.get("name") == AGENT_NAME for agent in resp.json().get("data", resp.json().get("value", [])))


def build_definition() -> dict:
    search_service = os.environ["AZURE_SEARCH_SERVICE_NAME"]
    workspace_id = os.environ["FABRIC_WORKSPACE_ID"]
    data_agent_id = os.environ["FABRIC_DATA_AGENT_ID"]
    ontology_item_id = os.environ["FABRIC_ONTOLOGY_ITEM_ID"]

    return {
        "model": os.environ["AZURE_FOUNDRY_MODEL_DEPLOYMENT_NAME"],
        "kind": "prompt",
        "instructions": (
            "You are the chocolate factory's assistant, grounded in "
            "three tools: the knowledge_base tool for policy/definition "
            "questions (what counts as an overdue invoice, substitution "
            "rules, quality metric definitions), the fabric_data_agent "
            "tool for Factory/Quality telemetry aggregates (quality "
            "checks, line status), and the fabric_iq_ontology tool for "
            "structured relationship questions across Factory, Quality, "
            "Supply Chain, and ERP entities (e.g. which supplier feeds "
            "which recipe, which batch used which line). Pick the tool "
            "that matches the question's shape, cite which tool you "
            "used, and say plainly when a question needs data none of "
            "these three cover rather than guessing."
        ),
        "tools": [
            {
                "type": "mcp",
                "server_label": "knowledge_base",
                "server_url": f"https://{search_service}.search.windows.net/knowledgebases/chocolate-factory-kb/mcp?api-version=2026-05-01-preview",
                "require_approval": "never",
                "allowed_tools": ["knowledge_base_retrieve"],
                "project_connection_id": "chocolate-factory-kb",
            },
            {
                "type": "mcp",
                "server_label": "fabric_data_agent",
                "server_url": f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{workspace_id}/dataagents/{data_agent_id}/agent",
                "project_connection_id": "fabric-data-agent",
                "require_approval": "never",
            },
            {
                "type": "mcp",
                "server_label": "fabric_iq_ontology",
                "server_url": f"https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{workspace_id}/items/{ontology_item_id}/ontologyEndpoint",
                "project_connection_id": "fabric-iq-ontology",
                "require_approval": "never",
            },
        ],
    }


def main() -> None:
    account = os.environ["AZURE_FOUNDRY_ACCOUNT_NAME"]
    project = os.environ["AZURE_FOUNDRY_PROJECT_NAME"]
    project_endpoint = f"https://{account}.services.ai.azure.com/api/projects/{project}"

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {get_token()}"
    session.headers["Content-Type"] = "application/json"

    definition = build_definition()

    if agent_exists(session, project_endpoint):
        # Updates create a new version under the existing agent name --
        # there's no in-place PATCH of a version's definition (verified
        # live: PATCH .../agents/{id} returns 200 but silently leaves
        # the existing version untouched).
        print(f"creating new version of existing agent {AGENT_NAME!r}")
        resp = session.post(
            f"{project_endpoint}/agents/{AGENT_NAME}/versions",
            params={"api-version": API_VERSION},
            data=json.dumps({"definition": definition}),
        )
    else:
        print("creating new agent")
        resp = session.post(
            f"{project_endpoint}/agents",
            params={"api-version": API_VERSION},
            data=json.dumps({"name": AGENT_NAME, "definition": definition}),
        )

    resp.raise_for_status()
    body = resp.json()
    print(f"agent {AGENT_NAME!r} version {body.get('version', '?')} ready")
    print(json.dumps(body, indent=2))


if __name__ == "__main__":
    main()
