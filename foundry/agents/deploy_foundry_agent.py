#!/usr/bin/env python3
"""Create (or update) Foundry Agent Service agents for the FabCon
demo's agent spine.

Two shapes, selected by the `AGENT_PROFILE` env var (default
`"generalist"`):

- **`generalist`** (`build_definition_generalist`) -- one agent wired
  to all three domains/tools, the original "leanest starting point"
  (see ../../.claude/plans) and the literal fallback body: byte-
  identical to what this file always deployed, kept unchanged so
  `terraform apply` with `enable_agent_split=false` (infra/azure.tf)
  behaves exactly as before this split was added.
- **`specialist_factory_quality`** / **`specialist_supply_chain_erp`**
  (`build_definition_specialist_factory_quality` /
  `build_definition_specialist_supply_chain_erp`) -- the two Prompt
  agents behind a Hosted Coordinator (`../agents/coordinator/`) when
  `enable_agent_split=true`. Same `model`/`kind: "prompt"` shape as the
  generalist, narrower instructions, and a reduced tool set per domain
  (see each builder's docstring below). Added per the plan in
  `~/.claude/plans/please-analyze-current-project-majestic-meteor.md`
  ("Phase 2"), reusing the profile-branching design first speced (but
  not implemented) in the prior spike,
  `~/.claude/plans/now-let-s-deploy-the-reactive-toucan.md`.

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
    AGENT_PROFILE=generalist|specialist_factory_quality|specialist_supply_chain_erp \\
        uv run --with azure-identity --with requests \\
        deploy_foundry_agent.py
"""

import json
import os

import requests
from azure.identity import AzureCliCredential

API_VERSION = "v1"

# Profile -> (agent name, definition builder). "generalist" is the
# fallback/default -- its agent name is unchanged from before this
# split existed, so an untouched `terraform apply` (AGENT_PROFILE unset)
# keeps deploying the exact same agent it always has.
_PROFILES = {
    "generalist": "chocolate-factory-agent",
    "specialist_factory_quality": "chocolate-factory-specialist-factory-quality",
    "specialist_supply_chain_erp": "chocolate-factory-specialist-supply-chain-erp",
}


def get_token() -> str:
    return AzureCliCredential().get_token("https://ai.azure.com/.default").token


def agent_exists(session: requests.Session, project_endpoint: str, agent_name: str) -> bool:
    resp = session.get(f"{project_endpoint}/agents", params={"api-version": API_VERSION})
    resp.raise_for_status()
    return any(agent.get("name") == agent_name for agent in resp.json().get("data", resp.json().get("value", [])))


def build_definition_generalist() -> dict:
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


def build_definition_specialist_factory_quality() -> dict:
    """Factory/Quality specialist -- one of two Prompt agents behind the
    Hosted Coordinator (../agents/coordinator/) when
    `enable_agent_split=true`. Domain: production lines, batches, sensor
    telemetry, quality checks, and line-status/downtime events -- the
    "Factory / Quality" table group in doc/cacao-data-model.md §4 and
    the streaming Eventhouse plane in doc/cacao-medallion-layers.md §0.
    Tools are narrowed to `knowledge_base` (policy/definition questions)
    and `fabric_data_agent` (the Eventhouse-backed telemetry aggregates)
    -- `fabric_iq_ontology` is dropped since Supply Chain/ERP structural
    relationships are the other specialist's job, not this one's.
    """
    search_service = os.environ["AZURE_SEARCH_SERVICE_NAME"]
    workspace_id = os.environ["FABRIC_WORKSPACE_ID"]
    data_agent_id = os.environ["FABRIC_DATA_AGENT_ID"]

    return {
        "model": os.environ["AZURE_FOUNDRY_MODEL_DEPLOYMENT_NAME"],
        "kind": "prompt",
        "instructions": (
            "You are the chocolate factory's Factory/Quality specialist. "
            "You answer questions about production lines, batches, "
            "sensor telemetry, quality checks (defect rate, pass/fail "
            "results), and line status/downtime events (running vs. "
            "down, and why -- changeover, scheduled maintenance, "
            "unplanned stops) across the four factories (EMEA-BCN, "
            "NA-CHI, LATAM-GRU, APAC-SIN) and their production lines. "
            "Use the knowledge_base tool for policy/definition questions "
            "(e.g. what counts as a quality-check failure) and the "
            "fabric_data_agent tool for telemetry aggregates (quality "
            "checks failed today, line status, downtime anomalies). You "
            "do not have Supply Chain or ERP data (suppliers, materials, "
            "inventory, shipments, customers, orders, invoices) -- say "
            "so plainly rather than guessing if asked about those."
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
        ],
    }


def build_definition_specialist_supply_chain_erp() -> dict:
    """Supply Chain/ERP specialist -- the other Prompt agent behind the
    Hosted Coordinator when `enable_agent_split=true`. Domain: suppliers,
    raw materials, inventory, inter-factory shipments, customers, sales
    orders, order lines, and invoices -- the "Supply Chain" and
    "ERP / Orders" table groups in doc/cacao-data-model.md §4. Tools are
    narrowed to `knowledge_base` and `fabric_iq_ontology` (the structured
    relationship graph across these entities) -- `fabric_data_agent` is
    dropped since Factory/Quality telemetry aggregates are the other
    specialist's job, not this one's.
    """
    search_service = os.environ["AZURE_SEARCH_SERVICE_NAME"]
    workspace_id = os.environ["FABRIC_WORKSPACE_ID"]
    ontology_item_id = os.environ["FABRIC_ONTOLOGY_ITEM_ID"]

    return {
        "model": os.environ["AZURE_FOUNDRY_MODEL_DEPLOYMENT_NAME"],
        "kind": "prompt",
        "instructions": (
            "You are the chocolate factory's Supply Chain/ERP specialist. "
            "You answer questions about suppliers (name, country, "
            "material type, rating), raw materials and inventory levels "
            "per factory, inter-factory/distribution shipments, "
            "customers, sales orders and order lines, and invoices (e.g. "
            "what counts as an overdue invoice). Use the knowledge_base "
            "tool for policy/definition questions and the "
            "fabric_iq_ontology tool for structured relationship "
            "questions across these entities (e.g. which supplier feeds "
            "which material, which shipment carried which batch, which "
            "order line belongs to which invoice). You do not have "
            "Factory/Quality telemetry data (production lines, sensor "
            "readings, quality checks, line status) -- say so plainly "
            "rather than guessing if asked about those."
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
                "server_label": "fabric_iq_ontology",
                "server_url": f"https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{workspace_id}/items/{ontology_item_id}/ontologyEndpoint",
                "project_connection_id": "fabric-iq-ontology",
                "require_approval": "never",
            },
        ],
    }


_BUILDERS = {
    "generalist": build_definition_generalist,
    "specialist_factory_quality": build_definition_specialist_factory_quality,
    "specialist_supply_chain_erp": build_definition_specialist_supply_chain_erp,
}


def main() -> None:
    account = os.environ["AZURE_FOUNDRY_ACCOUNT_NAME"]
    project = os.environ["AZURE_FOUNDRY_PROJECT_NAME"]
    project_endpoint = f"https://{account}.services.ai.azure.com/api/projects/{project}"

    profile = os.environ.get("AGENT_PROFILE", "generalist")
    if profile not in _PROFILES:
        raise SystemExit(f"Unknown AGENT_PROFILE {profile!r}; expected one of {sorted(_PROFILES)}")
    agent_name = _PROFILES[profile]
    build_definition = _BUILDERS[profile]

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {get_token()}"
    session.headers["Content-Type"] = "application/json"

    definition = build_definition()

    if agent_exists(session, project_endpoint, agent_name):
        # Updates create a new version under the existing agent name --
        # there's no in-place PATCH of a version's definition (verified
        # live: PATCH .../agents/{id} returns 200 but silently leaves
        # the existing version untouched).
        print(f"creating new version of existing agent {agent_name!r}")
        resp = session.post(
            f"{project_endpoint}/agents/{agent_name}/versions",
            params={"api-version": API_VERSION},
            data=json.dumps({"definition": definition}),
        )
    else:
        print("creating new agent")
        resp = session.post(
            f"{project_endpoint}/agents",
            params={"api-version": API_VERSION},
            data=json.dumps({"name": agent_name, "definition": definition}),
        )

    resp.raise_for_status()
    body = resp.json()
    print(f"agent {agent_name!r} version {body.get('version', '?')} ready")
    print(json.dumps(body, indent=2))


if __name__ == "__main__":
    main()
