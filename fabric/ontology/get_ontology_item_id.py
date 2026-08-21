#!/usr/bin/env python3
"""Terraform `data "external"` helper: looks up the already-deployed
Fabric IQ Ontology item's ID by display name, for use as the
Ontology MCP endpoint's `{ontology-item-ID}` path segment
(`infra/azure.tf`'s `azapi_resource.fabric_iq_ontology_mcp_connection`).

Reuses deploy_fabric_iq_ontology.py's own displayName/lookup
convention rather than introducing a second one.

Protocol: reads a JSON object on stdin (`{"workspace_id": "..."}`,
Terraform's `query` argument), writes `{"id": "..."}` to stdout.
https://developer.hashicorp.com/terraform/language/data-sources/external

Usage:
    echo '{"workspace_id": "..."}' | uv run --with azure-identity \\
        --with requests get_ontology_item_id.py
"""

import json
import sys

import requests
from azure.identity import AzureCliCredential

DISPLAY_NAME = "ChocolateFactory"
API_BASE = "https://api.fabric.microsoft.com/v1"


def main() -> None:
    query = json.load(sys.stdin)
    workspace_id = query["workspace_id"]

    token = AzureCliCredential().get_token("https://api.fabric.microsoft.com/.default").token
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"

    resp = session.get(f"{API_BASE}/workspaces/{workspace_id}/items", params={"type": "Ontology"})
    resp.raise_for_status()
    for item in resp.json().get("value", []):
        if item["displayName"] == DISPLAY_NAME:
            json.dump({"id": item["id"]}, sys.stdout)
            return

    raise SystemExit(f"no Ontology item named {DISPLAY_NAME!r} found in workspace {workspace_id}")


if __name__ == "__main__":
    main()
