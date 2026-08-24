#!/usr/bin/env python3
"""Enable incoming Agent-to-Agent (A2A) on the two specialist agents
(NOT the generalist/coordinator) so the Hosted Coordinator
(../agents/coordinator/) can call them directly over A2A.

This is the incoming-A2A half of the pattern the prior spike
(`~/.claude/plans/now-let-s-deploy-the-reactive-toucan.md`) designed
and partly verified live: PATCHing `agent_card` + `agent_endpoint` onto
a target agent, confirmed there with `chocolate-factory-agent` as a
stand-in specialist, `agent_card`/`agent_endpoint` PATCH shape taken
verbatim from that spike's Phase 1 findings. This file was planned
there but never written, since that spike stopped at Checkpoint 1 (the
`a2a_preview` *tool* was the thing that never worked, not the
underlying A2A protocol or the incoming-A2A PATCH itself -- see that
plan's "Outcome" section). The follow-up plan
(`~/.claude/plans/please-analyze-current-project-majestic-meteor.md`)
reopens A2A with a Hosted-agent Coordinator calling out over plain HTTP
instead of the buggy `a2a_preview` tool, so this file is scoped to
exactly the incoming-A2A PATCH -- no toolbox creation, no `RemoteA2A`
connection, since the Coordinator doesn't go through a Foundry
connection object at all.

**New finding this session, not documented anywhere else in the repo**:
the A2A endpoint is asynchronous. `message/send` returns immediately
with `result.status.state: "submitted"` and a task `id`, NOT the
answer -- callers must poll `tasks/get`
(`{"jsonrpc":"2.0","id":"...","method":"tasks/get","params":{"id":"<task-id>"}}`)
until `status.state == "completed"`, then read the answer from
`result.artifacts[].parts[].text`. See ../agents/coordinator's main.py
for the calling side of this, and ./README.md for the fuller writeup.

Auth convention matches deploy_foundry_agent.py: `AzureCliCredential`,
`https://ai.azure.com/.default` scope, a plain `requests` session --
this PATCH is on the same `agents` data-plane API, still with no
Terraform/azapi equivalent.

Usage:
    AZURE_FOUNDRY_ACCOUNT_NAME=... AZURE_FOUNDRY_PROJECT_NAME=... \\
        uv run --with azure-identity --with requests \\
        deploy_a2a.py
"""

import json
import os

import requests
from azure.identity import AzureCliCredential

API_VERSION = "v1"

# Must match deploy_foundry_agent.py's AGENT_NAME derivation for these
# two profiles. Only the specialists get incoming A2A -- the Coordinator
# is a caller, not a callee, so it's deliberately left off this list.
SPECIALIST_AGENT_CARDS = {
    "chocolate-factory-specialist-factory-quality": {
        "description": (
            "Factory/Quality specialist for the chocolate factory demo. "
            "Answers questions about production lines, batches, sensor "
            "telemetry, quality checks (defect rate, pass/fail results), "
            "and line status/downtime events (running vs. down, and "
            "why) across the four factories (EMEA-BCN, NA-CHI, "
            "LATAM-GRU, APAC-SIN). Does not have Supply Chain or ERP "
            "data (suppliers, materials, inventory, shipments, "
            "customers, orders, invoices)."
        ),
        "version": "1.0",
        "skills": [
            {
                "id": "factory-quality-telemetry",
                "name": "Factory/Quality telemetry and quality checks",
                "description": (
                    "Answers questions grounded in production-line "
                    "telemetry: quality-check pass/fail and defect "
                    "rates, line running/down status and downtime "
                    "reasons (changeover, scheduled maintenance, "
                    "unplanned stops), and batch/sensor-reading "
                    "aggregates, per factory and production line."
                ),
            }
        ],
    },
    "chocolate-factory-specialist-supply-chain-erp": {
        "description": (
            "Supply Chain/ERP specialist for the chocolate factory demo. "
            "Answers questions about suppliers, raw materials and "
            "inventory levels per factory, inter-factory/distribution "
            "shipments, customers, sales orders and order lines, and "
            "invoices. Does not have Factory/Quality telemetry data "
            "(production lines, sensor readings, quality checks, line "
            "status)."
        ),
        "version": "1.0",
        "skills": [
            {
                "id": "supply-chain-erp-relationships",
                "name": "Supply Chain and ERP/Orders relationships",
                "description": (
                    "Answers questions grounded in the Supply Chain and "
                    "ERP/Orders entity graph: which supplier feeds which "
                    "material, factory inventory and reorder levels, "
                    "which shipment carried which batch, and "
                    "customer/order/invoice relationships (e.g. what "
                    "counts as an overdue invoice)."
                ),
            }
        ],
    },
}


def get_token() -> str:
    return AzureCliCredential().get_token("https://ai.azure.com/.default").token


def enable_incoming_a2a(session: requests.Session, project_endpoint: str, agent_name: str, agent_card: dict) -> None:
    body = {
        "agent_card": agent_card,
        "agent_endpoint": {"protocol_configuration": {"responses": {}, "a2a": {}}},
    }
    print(f"enabling incoming A2A on {agent_name!r}")
    resp = session.patch(
        f"{project_endpoint}/agents/{agent_name}",
        params={"api-version": API_VERSION},
        data=json.dumps(body),
    )
    resp.raise_for_status()
    print(json.dumps(resp.json(), indent=2))

    confirm = session.get(
        f"{project_endpoint}/agents/{agent_name}/endpoint/protocols/a2a/agentCard/v1.0",
        params={"api-version": API_VERSION},
    )
    confirm.raise_for_status()
    print(f"confirmed agent card for {agent_name!r}:")
    print(json.dumps(confirm.json(), indent=2))


def main() -> None:
    account = os.environ["AZURE_FOUNDRY_ACCOUNT_NAME"]
    project = os.environ["AZURE_FOUNDRY_PROJECT_NAME"]
    project_endpoint = f"https://{account}.services.ai.azure.com/api/projects/{project}"

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {get_token()}"
    session.headers["Content-Type"] = "application/json"

    for agent_name, agent_card in SPECIALIST_AGENT_CARDS.items():
        enable_incoming_a2a(session, project_endpoint, agent_name, agent_card)


if __name__ == "__main__":
    main()
