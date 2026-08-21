#!/usr/bin/env python3
"""Create (or update) the single Foundry Agent Service agent for the
FabCon demo's agent spine -- one generalist agent wired to two domains
via MCP tools so far, per the plan's "leanest starting point" (see
../../.claude/plans -- Coordinator + specialists is a later decision,
made only if this single agent's answer quality needs it).

Direct Fabric/Foundry REST calls (AzureCliCredential), same convention
as deploy_fabric_iq_ontology.py and deploy_search_indexer.py -- the
Foundry Agent Service's exact `agents` API shape isn't in any
Terraform provider, and its own docs (foundry-iq-connect) call it via
raw `requests` too, not an SDK method.

Tools, each a generic `mcp` tool pointed at an already-created project
connection (infra/azure.tf) rather than a bare server_url + static
bearer header -- Microsoft's own docs say static secrets aren't
accepted in MCP tool headers; routing through a stored connection is
the documented alternative:

- chocolate-factory-kb   -- Foundry IQ knowledge base (Factory/Quality,
  Supply Chain, ERP domain docs), allowed_tools restricted to
  knowledge_base_retrieve. Required the Search service's authOptions
  switched from apiKeyOnly to aadOrApiKey (infra/azure.tf) -- RBAC role
  assignments alone don't matter if the service rejects AAD tokens
  outright.
- fabric-data-agent      -- the shared Fabric Data Agent (Eventhouse:
  quality checks, line status).

The Fabric IQ Ontology tool is deliberately NOT included yet. It's a
different Fabric item type with a different auth model: unlike the
above two (ProjectManagedIdentity, a pure service-to-service flow),
Microsoft's own docs
(https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/fabric-iq)
say ontology connections require Microsoft Entra delegated
(On-Behalf-Of) auth via a BYO Entra app or managed OAuth -- there is no
application-only/managed-identity path for it at all. Getting there
needs: (1) a dedicated Entra app registration with Power BI Service
delegated permissions Item.Execute.All + Item.Read.All and tenant-wide
admin consent (done -- app f750d801-54b1-4d98-9c95-367550802361), then
(2) a project connection with authType OAuth2 carrying that app's
client ID/secret plus the Entra authorization/token/refresh URLs and
Power BI scopes. Step (2) is paused: the Foundry portal's own "New
connection > Fabric IQ" wizard collects Token URL/Refresh URL/Scopes
fields that don't appear anywhere in the documented
`Microsoft.MachineLearningServices/workspaces/connections` ARM schema
(WorkspaceConnectionOAuth2 only has authUrl/clientId/clientSecret/
developerToken/password/refreshToken/tenantId/username) -- the
portal's extra mapping isn't publicly documented, so this one
connection needs to be created through the Foundry portal UI by hand
rather than guessed at over raw REST. Once that connection exists (any
name), add a `fabric_iq_preview`-type tool (not `mcp`) referencing it
by `project_connection_id`, pointed at
`https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{workspace}/items/{ontologyItemId}/ontologyEndpoint`.
The first query from each user will need an interactive OAuth consent
(delegated auth, not shared identity) -- expect a `CONSENT_REQUIRED`
response with a consent link on first use per user.

Note also that even the Agent 365-hosted ontology MCP endpoint
(agent365.svc.cloud.microsoft/.../mcp_FabricIQOntology/...), which an
earlier version of this script pointed at, is the WRONG endpoint for a
Foundry agent -- that one is for Copilot Studio/M365 Copilot consumers
and is gated to Frontier-program tenants. The native
api.fabric.microsoft.com one above is the one Foundry agents should
use.

Usage:
    AZURE_FOUNDRY_ACCOUNT_NAME=... AZURE_FOUNDRY_PROJECT_NAME=... \\
    AZURE_FOUNDRY_MODEL_DEPLOYMENT_NAME=... FABRIC_WORKSPACE_ID=... \\
    FABRIC_DATA_AGENT_ID=... AZURE_SEARCH_SERVICE_NAME=... \\
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
    workspace_id = os.environ["FABRIC_WORKSPACE_ID"]
    data_agent_id = os.environ["FABRIC_DATA_AGENT_ID"]
    search_service = os.environ["AZURE_SEARCH_SERVICE_NAME"]

    return {
        "model": os.environ["AZURE_FOUNDRY_MODEL_DEPLOYMENT_NAME"],
        "kind": "prompt",
        "instructions": (
            "You are the chocolate factory's assistant, grounded in two "
            "tools: the knowledge_base tool for policy/definition "
            "questions (what counts as an overdue invoice, substitution "
            "rules, quality metric definitions), and the fabric_data_agent "
            "tool for Factory/Quality telemetry aggregates (quality "
            "checks, line status). Pick the tool that matches the "
            "question's shape, cite which tool you used, and say plainly "
            "when a question needs data neither of these two cover "
            "(e.g. Supply Chain/ERP relationship questions -- the "
            "Ontology tool for those isn't wired up yet) rather than "
            "guessing."
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
                "require_approval": "never",
                "project_connection_id": "fabric-data-agent",
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
