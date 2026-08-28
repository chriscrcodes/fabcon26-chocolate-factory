#!/usr/bin/env python3
"""Manually pause or resume the Fabric capacity (`infra/fabric.tf`'s
`azapi_resource.fabric_capacity`) -- the same `suspend`/`resume` ARM
actions the nightly auto-pause runbook calls
(`azurerm_automation_runbook.pause_fabric_capacity`), for local use
before/after a demo or rehearsal session. Resume isn't automated: demo
timing is irregular, so run this manually before you need the
capacity, and either let the nightly schedule pause it later or run
`pause` yourself when you're done for the day.

Usage:
    AZURE_SUBSCRIPTION_ID=... AZURE_RESOURCE_GROUP=... FABRIC_CAPACITY_NAME=... \\
        uv run --with azure-identity --with requests manage_capacity.py resume
    uv run --with azure-identity --with requests manage_capacity.py pause
"""

import os
import sys
import time

import requests
from azure.identity import AzureCliCredential

API_VERSION = "2023-11-01"


def get_token() -> str:
    return AzureCliCredential().get_token("https://management.azure.com/.default").token


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("pause", "resume"):
        raise SystemExit(f"usage: {sys.argv[0]} pause|resume")
    action = "suspend" if sys.argv[1] == "pause" else "resume"

    subscription_id = os.environ["AZURE_SUBSCRIPTION_ID"]
    resource_group = os.environ["AZURE_RESOURCE_GROUP"]
    capacity_name = os.environ["FABRIC_CAPACITY_NAME"]

    capacity_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Fabric/capacities/{capacity_name}"
    )

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {get_token()}"

    target_state = "Active" if action == "resume" else "Paused"
    state_resp = session.get(f"https://management.azure.com{capacity_id}", params={"api-version": API_VERSION})
    state_resp.raise_for_status()
    current_state = state_resp.json().get("properties", {}).get("state")
    if current_state == target_state:
        print(f"{capacity_name} is already {current_state}")
        return

    print(f"{sys.argv[1]}ing {capacity_name}...")
    # This endpoint 400s ("Service is not ready to be updated") if the
    # capacity is already in the target state -- checked above -- and
    # 411s on a POST with no body at all (no Content-Length header) --
    # an explicit empty JSON body forces `requests` to send one.
    resp = session.post(
        f"https://management.azure.com{capacity_id}/{action}", params={"api-version": API_VERSION}, json={}
    )
    resp.raise_for_status()

    for _ in range(30):
        state_resp = session.get(f"https://management.azure.com{capacity_id}", params={"api-version": API_VERSION})
        state_resp.raise_for_status()
        state = state_resp.json().get("properties", {}).get("state")
        print(f"state: {state}")
        if state in ("Active", "Paused"):
            return
        time.sleep(5)

    raise SystemExit(f"{capacity_name} did not reach a stable state in time")


if __name__ == "__main__":
    main()
