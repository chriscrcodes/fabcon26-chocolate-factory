#!/usr/bin/env python3
"""Publish the Fabric Data Agent (`fabric_data_agent.business`,
`infra/fabric.tf`) -- a draft-only Data Agent can't be queried at all
(the MCP endpoint 404s "while enumerating tools" until this runs), but
neither `fabric_data_agent`'s Terraform resource nor anything else in
this repo previously called this -- confirmed live on a fresh
redeploy: the agent worked in the original deployment only because
this had been run by hand at some point and never captured anywhere.

Usage:
    FABRIC_WORKSPACE_ID=... FABRIC_DATA_AGENT_ID=... \\
        uv run --with azure-identity --with requests \\
        publish_data_agent.py
"""

import os

import requests
from azure.identity import AzureCliCredential

API_BASE = "https://api.fabric.microsoft.com/v1"


def main() -> None:
    workspace_id = os.environ["FABRIC_WORKSPACE_ID"]
    data_agent_id = os.environ["FABRIC_DATA_AGENT_ID"]

    token = AzureCliCredential().get_token("https://api.fabric.microsoft.com/.default").token
    resp = requests.post(
        f"{API_BASE}/workspaces/{workspace_id}/dataAgents/{data_agent_id}/staging/publish",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"description": "Published by infra/fabric.tf's null_resource.publish_data_agent."},
    )
    resp.raise_for_status()
    print(f"published Data Agent {data_agent_id!r}")


if __name__ == "__main__":
    main()
