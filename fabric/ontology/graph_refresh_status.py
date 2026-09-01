#!/usr/bin/env python3
"""Shared read-only lookup of the Fabric IQ Ontology's GraphModel refresh
state -- factored out of deploy_fabric_iq_ontology.py's
`print_refresh_reminder` so the same check can also run as a preflight in
test_agent_questions.py, before timing live agent queries.

There is no Fabric REST API to trigger a GraphModel refresh (confirmed in
deploy_fabric_iq_ontology.py) -- this module only reports the last known
refresh job's status, it cannot fix a stale graph.
"""

import requests

API_BASE = "https://api.fabric.microsoft.com/v1"
DISPLAY_NAME = "ChocolateFactory"


def get_last_refresh_job(session: requests.Session, workspace_id: str) -> dict | None:
    """Return {"status": ..., "timestamp": ...} for the most recent
    GraphModel refresh job, or None if the GraphModel item or its job
    history can't be found/queried (e.g. transient API error, or no
    refresh has ever run).
    """
    try:
        resp = session.get(f"{API_BASE}/workspaces/{workspace_id}/items", params={"type": "GraphModel"})
        resp.raise_for_status()
        graph_model_id = None
        for item in resp.json().get("value", []):
            if item["displayName"].startswith(f"{DISPLAY_NAME}_graph_"):
                graph_model_id = item["id"]
                break
        if not graph_model_id:
            return None

        resp = session.get(f"{API_BASE}/workspaces/{workspace_id}/items/{graph_model_id}/jobs/instances")
        resp.raise_for_status()
        jobs = resp.json().get("value", [])
        if not jobs:
            return None

        latest = jobs[0]
        return {
            "status": latest.get("status"),
            "timestamp": latest.get("endTimeUtc") or latest.get("startTimeUtc"),
        }
    except requests.RequestException:
        return None


def describe_last_refresh_job(job: dict | None) -> str:
    if job is None:
        return "unknown (no refresh has ever run, or the lookup failed)"
    return f"{job['status']} at {job['timestamp']}"
