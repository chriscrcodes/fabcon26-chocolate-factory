"""Fetch the schema (entity types, relationship types, bound/unbound) of the
currently deployed Fabric IQ Ontology item directly from the Fabric REST API,
and write it to deployed_schema.json for build_graph_data.py to consume.

Unlike ontology_config.json (the human-authored source), this reflects
exactly what's live -- including Fabric-specific splits, e.g. sensor_reading
becomes one entity type per production stage (SensorReadingGrinding,
SensorReadingConching, ...) in the deployed definition.

Requires: az login, with access to the Fabric workspace. The Fabric capacity
must be resumed (not paused) or getDefinition calls fail/timeout.

Usage:
    FABRIC_WORKSPACE_ID=... uv run --with azure-identity --with requests \\
        fetch_deployed_schema.py
"""

import base64
import json
import os
import re
import time
from pathlib import Path

import requests
from azure.identity import AzureCliCredential

API_BASE = "https://api.fabric.microsoft.com/v1"
DISPLAY_NAME = "ChocolateFactory"
OUT_PATH = Path(__file__).resolve().parent / "deployed_schema.json"


def get_session() -> requests.Session:
    token = AzureCliCredential().get_token("https://api.fabric.microsoft.com/.default").token
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    return session


def find_ontology_item(session: requests.Session, workspace_id: str) -> str:
    resp = session.get(f"{API_BASE}/workspaces/{workspace_id}/items", params={"type": "Ontology"})
    resp.raise_for_status()
    for item in resp.json().get("value", []):
        if item["displayName"] == DISPLAY_NAME:
            return item["id"]
    raise SystemExit(f"no Ontology item named {DISPLAY_NAME!r} in workspace {workspace_id}")


def get_definition(session: requests.Session, workspace_id: str, item_id: str) -> dict:
    resp = session.post(f"{API_BASE}/workspaces/{workspace_id}/items/{item_id}/getDefinition")
    if resp.status_code != 202:
        resp.raise_for_status()
        return resp.json()

    op_url = resp.headers["Location"]
    while True:
        op = session.get(op_url)
        op.raise_for_status()
        body = op.json()
        if body["status"] == "Succeeded":
            result = session.get(f"{op_url}/result")
            result.raise_for_status()
            return result.json()
        if body["status"] == "Failed":
            raise SystemExit(f"getDefinition failed: {body}")
        time.sleep(3)


def main() -> None:
    workspace_id = os.environ["FABRIC_WORKSPACE_ID"]
    session = get_session()
    item_id = find_ontology_item(session, workspace_id)
    definition = get_definition(session, workspace_id, item_id)

    parts = definition["definition"]["parts"]
    entity_parts = [p for p in parts if re.match(r"EntityTypes/[^/]+/definition\.json$", p["path"])]
    rel_parts = [p for p in parts if re.match(r"RelationshipTypes/[^/]+/definition\.json$", p["path"])]
    bound_rel_dirs = {p["path"].split("/")[1] for p in parts if "/Contextualizations/" in p["path"]}

    id_to_name = {}
    for p in entity_parts:
        d = json.loads(base64.b64decode(p["payload"]).decode("utf-8"))
        id_to_name[d["id"]] = d["name"]

    relationships = []
    for p in rel_parts:
        rel_id = p["path"].split("/")[1]
        d = json.loads(base64.b64decode(p["payload"]).decode("utf-8"))
        relationships.append(
            {
                "name": d["name"],
                "source": id_to_name[d["source"]["entityTypeId"]],
                "target": id_to_name[d["target"]["entityTypeId"]],
                "bound": rel_id in bound_rel_dirs,
            }
        )

    out = {
        "item_id": item_id,
        "entities": sorted(id_to_name.values()),
        "relationships": relationships,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    bound_count = sum(1 for r in relationships if r["bound"])
    print(
        f"wrote {OUT_PATH} -- {len(out['entities'])} entity types, "
        f"{len(relationships)} relationship types ({bound_count} bound)"
    )


if __name__ == "__main__":
    main()
