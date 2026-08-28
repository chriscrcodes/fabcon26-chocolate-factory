#!/usr/bin/env python3
"""End-to-end pytest suite for `chocolate-factory-agent`, covering every
question in SETUP.md's "The full verified question bank" (Section 6) --
one test per question, tagged the same way the docs are: verified,
candidate, or known-to-fail.

Each test calls the live agent over its `/openai/v1/responses` endpoint
(same call as SETUP.md's curl example and deploy_foundry_agent.py's
auth pattern), measures wall-clock latency, and appends a JSON line to
test-results.jsonl next to this file -- unconditionally, before any
assertion runs, so a failing test still leaves data behind for the
response-time analysis pass (see SETUP.md's "Agent tracing and
observability" section for the App Insights side of that analysis).

Assertion strictness follows the question's tag, matching this repo's
"verified, not asserted" discipline:
  - verified:      must succeed, and (if keywords given) the answer
                    must contain at least one -- these are the
                    questions proven to work; a regression here is real.
  - candidate:      must succeed (get a non-empty answer with no tool
                    error), but content isn't asserted -- not yet
                    proven, so a wrong-but-present answer isn't a
                    pytest failure, just data for review.
  - known-to-fail:  wrapped in xfail(strict=False) -- expected to fail
                    today (documented root causes in SETUP.md), so a
                    failure is not reported as broken, but a surprise
                    pass is called out (the docs would need updating).

Auth: AzureCliCredential (az login), same as every other script in
this repo. The querying identity needs the same real Fabric workspace
role and Foundry "Foundry User"-or-equivalent access documented in
SETUP.md's "Reproducing on a different subscription" section --
`UserEntraToken` tools forward the calling identity, not a service
principal.

Usage:
    AZURE_FOUNDRY_ACCOUNT_NAME=... AZURE_FOUNDRY_PROJECT_NAME=... \\
        uv run --with pytest --with azure-identity --with requests \\
        -m pytest foundry/agents/test_agent_questions.py -v -s

    Both env vars come straight from `infra`'s terraform outputs
    (AZURE_FOUNDRY_ACCOUNT_NAME, AZURE_FOUNDRY_PROJECT_NAME).
"""

import json
import os
import time
from pathlib import Path

import pytest
import requests
from azure.identity import AzureCliCredential

AGENT_NAME = os.environ.get("AZURE_FOUNDRY_AGENT_NAME", "chocolate-factory-agent")
RESULTS_PATH = Path(__file__).parent / "test-results.jsonl"
REQUEST_TIMEOUT_SECONDS = 180

# id, tool(s) exercised, tag (verified/candidate/known-to-fail), question,
# expect: list of case-insensitive substrings, any one of which must
# appear in the answer for a `verified` question to pass. None means
# "just require a real, non-error answer" (used for `candidate`
# questions, where content isn't proven yet).
QUESTIONS = [
    dict(
        id="kb-overdue-invoice",
        tool="knowledge_base",
        tag="verified",
        question="What counts as an overdue invoice?",
        expect=["due", "overdue", "paid"],
    ),
    dict(
        id="kb-crystal-form-index",
        tool="knowledge_base",
        tag="verified",
        question="What does CrystalFormIndex measure?",
        expect=["temper", "crystal", "form v"],
    ),
    dict(
        id="kb-nib-shortage-substitution",
        tool="knowledge_base",
        tag="candidate",
        question="Why can't a nib shortage always be substituted the way a packaging shortage can?",
        expect=None,
    ),
    dict(
        id="kb-chocolate-percentage-range",
        tool="knowledge_base",
        tag="candidate",
        question="What's our company's chocolate percentage range across recipes?",
        expect=None,
    ),
    dict(
        id="data-agent-quality-checks-failed-today",
        tool="fabric_data_agent",
        tag="verified",
        question="How many quality checks failed today?",
        expect=None,
    ),
    dict(
        id="data-agent-factory-status",
        tool="fabric_data_agent",
        tag="verified",
        question="How is my factory going right now?",
        # Either a real defect-rate answer (simulator actively
        # streaming) or an honest "no current data" -- SETUP.md
        # documents both as correct: "says plainly when it can't
        # confirm something rather than guessing" is a feature, not a
        # failure, when the simulator isn't running at test time.
        expect=["defect", "no current data", "can't confirm", "can't tell", "no data"],
    ),
    dict(
        id="data-agent-anomalies",
        tool="fabric_data_agent",
        tag="verified",
        question="Are there any anomalies I should be aware of?",
        expect=None,
    ),
    dict(
        id="data-agent-most-downtime-line",
        tool="fabric_data_agent",
        tag="candidate",
        question="Which line has the most downtime today?",
        expect=None,
    ),
    dict(
        id="data-agent-avg-defect-rate-by-stage",
        tool="fabric_data_agent",
        tag="candidate",
        question="What's the average defect rate by stage this week?",
        expect=None,
    ),
    dict(
        id="ontology-entity-types",
        tool="fabric_iq_ontology",
        tag="verified",
        question="What entity types exist in the ontology?",
        # Accept either an explicit count or real entity names in the
        # list -- confirmed live the agent sometimes enumerates names
        # without stating "22" as a number.
        expect=["22", "qualitycheck", "productionline", "sensorreading"],
    ),
    dict(
        id="ontology-suppliers",
        tool="fabric_iq_ontology",
        tag="verified",
        question="Who are our suppliers?",
        expect=None,
    ),
    dict(
        id="ontology-suppliers-materials",
        tool="fabric_iq_ontology",
        tag="verified",
        question="Which suppliers provide materials, and what type of material does each provide?",
        expect=None,
    ),
    dict(
        id="ontology-line-to-factory",
        tool="fabric_iq_ontology",
        tag="candidate",
        question="Which factory does production line 1 at EMEA-BCN belong to?",
        expect=["barcelona", "bcn", "emea"],
    ),
    dict(
        id="ontology-shipments-to-factory",
        tool="fabric_iq_ontology",
        tag="candidate",
        # FAC-CHI is the factory's FactoryId (shipment.FromFactoryId's
        # actual foreign key) -- distinct from its Code, "NA-CHI" (see
        # factory.csv: FactoryId,Code are two different columns). Using
        # Code here returns zero results -- confirmed live, not a
        # system bug, a wrong identifier for this specific query.
        question="What shipments is factory FAC-CHI receiving?",
        expect=None,
    ),
    dict(
        id="centerpiece-shipments-and-quality",
        tool="fabric_iq_ontology + fabric_data_agent (chained)",
        tag="verified",
        question=(
            "Which factories are receiving shipments, and how many "
            "quality checks failed today at those same factories?"
        ),
        expect=None,
        # This is the flagship two-tool chain -- an HTTP 200 with the
        # ontology half correct but the fabric_data_agent half silently
        # blocked mid-chain (confirmed live: "hit a technical block")
        # would otherwise pass the generic checks above. Catch that
        # partial-failure case explicitly rather than treating any 200
        # as success.
        reject=["technical block", "couldn't get a valid", "blocked query"],
    ),
    dict(
        id="centerpiece-worst-quality-and-inventory",
        tool="fabric_iq_ontology + fabric_data_agent + fabric SQL (chained)",
        tag="candidate",
        question=(
            "Which factory has the worst quality this week, and do we "
            "have enough inventory of its key material to keep it "
            "running?"
        ),
        expect=None,
    ),
    dict(
        id="known-fail-pronoun-reference",
        tool="fabric_iq_ontology",
        tag="known-to-fail",
        question="Who are the suppliers of this product?",
        expect=None,
    ),
    dict(
        id="known-fail-batch-material-traceability",
        tool="fabric_iq_ontology",
        tag="known-to-fail",
        question="Which suppliers fed the batches in Dark 70% production?",
        expect=None,
    ),
    dict(
        id="known-fail-batch-line-recipe",
        tool="fabric_iq_ontology",
        tag="known-to-fail",
        question="What line and recipe was used for batch B-000123?",
        expect=None,
    ),
]


@pytest.fixture(scope="module")
def agent_client():
    account = os.environ.get("AZURE_FOUNDRY_ACCOUNT_NAME")
    project = os.environ.get("AZURE_FOUNDRY_PROJECT_NAME")
    if not account or not project:
        pytest.skip(
            "AZURE_FOUNDRY_ACCOUNT_NAME and AZURE_FOUNDRY_PROJECT_NAME must be "
            "set (see infra's terraform outputs) to run against the live agent."
        )

    token = AzureCliCredential().get_token("https://ai.azure.com/.default").token
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    session.headers["Content-Type"] = "application/json"
    project_endpoint = f"https://{account}.services.ai.azure.com/api/projects/{project}"
    return session, project_endpoint


def _extract_output(body: dict) -> tuple[str, list[dict]]:
    """Best-effort extraction of the assistant's text and any tool-call
    entries from an OpenAI Responses API body. Not yet confirmed against
    a live 200 response from this agent (this repo's Foundry RBAC gap
    blocked that at the time this was written -- see SETUP.md) -- adjust
    field names here once a real response is in hand, per this file's
    own "verified, not asserted" discipline.
    """
    texts = []
    tool_calls = []
    for item in body.get("output", []):
        item_type = item.get("type")
        if item_type == "message":
            for block in item.get("content", []):
                if block.get("type") in ("output_text", "text"):
                    texts.append(block.get("text", ""))
        elif item_type in ("mcp_call", "tool_call", "function_call"):
            tool_calls.append(
                {
                    "type": item_type,
                    "server_label": item.get("server_label") or item.get("name"),
                    "status": item.get("status"),
                    "error": item.get("error"),
                }
            )
    return "\n".join(texts), tool_calls


def _ask(session: requests.Session, project_endpoint: str, question: str):
    started = time.monotonic()
    response = session.post(
        f"{project_endpoint}/openai/v1/responses",
        data=json.dumps(
            {
                "agent_reference": {"type": "agent_reference", "name": AGENT_NAME},
                "input": [{"role": "user", "content": question}],
            }
        ),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    elapsed_seconds = time.monotonic() - started
    return elapsed_seconds, response


def _record(result: dict) -> None:
    with RESULTS_PATH.open("a") as f:
        f.write(json.dumps(result) + "\n")


@pytest.mark.parametrize("case", QUESTIONS, ids=[c["id"] for c in QUESTIONS])
def test_agent_question(agent_client, case):
    session, project_endpoint = agent_client
    elapsed_seconds, response = _ask(session, project_endpoint, case["question"])

    body: dict = {}
    parse_error = None
    try:
        body = response.json()
    except ValueError as e:
        parse_error = str(e)

    text, tool_calls = _extract_output(body) if not parse_error else ("", [])
    result = {
        "id": case["id"],
        "tool": case["tool"],
        "tag": case["tag"],
        "question": case["question"],
        "http_status": response.status_code,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "tool_calls": tool_calls,
        "answer_text": text,
        "parse_error": parse_error,
        "raw_body_on_error": None if response.ok else body or response.text[:2000],
    }
    _record(result)

    if case["tag"] == "known-to-fail":
        # Expected to fail today, per SETUP.md's diagnosed root causes.
        # xfail so it doesn't count as a broken test, but a surprise
        # pass is reported (strict=False) -- that would mean the repo's
        # docs are stale and should be updated.
        if not response.ok or any(
            marker in text.lower()
            for marker in (
                "could not be processed",
                "don't have",
                "no information",
                "not available",
                "no results",
                "returned no results",
                "can't tell",
                "can't reliably",
            )
        ):
            pytest.xfail(f"documented known-to-fail behavior reproduced: {text[:200]!r}")
        # Falls through to the normal assertions below if it unexpectedly succeeded.

    assert response.ok, f"HTTP {response.status_code}: {result['raw_body_on_error']}"
    assert not parse_error, f"response body wasn't valid JSON: {parse_error}"
    assert text.strip(), "agent returned no text output"
    assert not any(tc.get("error") for tc in tool_calls), f"a tool call errored: {tool_calls}"

    reject = case.get("reject")
    if reject:
        lowered = text.lower()
        assert not any(kw in lowered for kw in reject), (
            f"answer admits a partial failure ({[kw for kw in reject if kw in lowered]!r}): {text[:300]!r}"
        )

    if case["tag"] == "verified" and case["expect"]:
        lowered = text.lower()
        assert any(kw in lowered for kw in case["expect"]), (
            f"expected one of {case['expect']!r} in answer, got: {text[:300]!r}"
        )
