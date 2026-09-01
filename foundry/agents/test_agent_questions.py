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

Before trusting a slow result as agent-side, follow SETUP.md's
"Measuring agent latency validly" checklist (pause the simulator, don't
run `terraform apply` concurrently, check the Fabric Capacity Metrics
app for throttling) -- this repo's own capacity is small and shared
across ingestion, materialization, GraphModel refreshes, and agent
queries, unthrottled against each other.

If AZURE_APP_INSIGHTS_NAME and AZURE_RESOURCE_GROUP are set, each
result also gets a best-effort `tool_call_durations` breakdown (per
server_label, in seconds) pulled from App Insights' `dependencies`
table, so a slow multi-tool question's time can be attributed to a
specific hop instead of only a total. And once per session, before any
fabric_iq_ontology-dependent question runs, the GraphModel's last
refresh job status is checked (see fabric/ontology/graph_refresh_status.py)
-- a stale/failed graph produces a diagnosed `graph_stale_warning` tag
in the result instead of an unexplained 400 mid-run.

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
                    A case may also carry `wrong_answer`: phrasing that
                    identifies a confident-but-wrong answer, for the
                    failures that arrive as answers rather than errors
                    and would otherwise pass every generic assertion.

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

    Optional, for the two latency-diagnostics additions -- both no-op
    (silently skipped) if unset:
        FABRIC_WORKSPACE_ID=...          # GraphModel freshness preflight
        AZURE_APP_INSIGHTS_NAME=... AZURE_RESOURCE_GROUP=...  # per-hop breakdown

    All four come straight from `infra`'s terraform outputs
    (AZURE_FOUNDRY_ACCOUNT_NAME, AZURE_FOUNDRY_PROJECT_NAME are required;
    the other two are optional).
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from azure.identity import AzureCliCredential

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "fabric" / "ontology"))
from graph_refresh_status import describe_last_refresh_job, get_last_refresh_job  # noqa: E402

AGENT_NAME = os.environ.get("AZURE_FOUNDRY_AGENT_NAME", "chocolate-factory-agent")
RESULTS_PATH = Path(__file__).parent / "test-results.jsonl"
REQUEST_TIMEOUT_SECONDS = 180
APP_INSIGHTS_NAME = os.environ.get("AZURE_APP_INSIGHTS_NAME")
APP_INSIGHTS_RESOURCE_GROUP = os.environ.get("AZURE_RESOURCE_GROUP")

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
        tag="verified",
        question="Why can't a nib shortage always be substituted the way a packaging shortage can?",
        expect=["nib", "grinding"],
    ),
    dict(
        id="kb-chocolate-percentage-range",
        tool="knowledge_base",
        # Stays candidate for a grounding reason, not a content one: the
        # answer is correct (0-70% cacao, matching recipe.csv's
        # CacaoPercent) but the agent routes to fabric_iq_ontology, not
        # the knowledge_base this question is meant to exercise.
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
        # The Data Agent's anomaly scan errors out inside the tool: the
        # agent reports "the telemetry scan hit a semantic error" and
        # declines to list anomalies rather than guessing. Reproduced on
        # three consecutive runs, with live telemetry flowing and a
        # freshly refreshed GraphModel, so this is the tool's behaviour
        # and not a data or capacity artifact.
        tag="known-to-fail",
        question="Are there any anomalies I should be aware of?",
        expect=None,
    ),
    dict(
        id="data-agent-most-downtime-line",
        tool="fabric_data_agent",
        tag="verified",
        question="Which line has the most downtime today?",
        # Needs live telemetry for "today" to be non-empty -- run the
        # simulator first, or this answers "no line status data for
        # today" and fails on these keywords, correctly.
        expect=["downtime"],
    ),
    dict(
        id="data-agent-avg-defect-rate-by-stage",
        tool="fabric_data_agent",
        tag="verified",
        question="What's the average defect rate by stage this week?",
        # Same live-telemetry precondition as most-downtime-line.
        expect=["tempering", "grinding", "packaging"],
    ),
    dict(
        id="ontology-entity-types",
        tool="fabric_iq_ontology",
        tag="verified",
        question="What entity types exist in the ontology?",
        # Accept either an explicit count or real entity names in the
        # list -- confirmed live the agent sometimes enumerates names
        # without stating "22" as a number.
        expect=["23", "qualitycheck", "productionline", "sensorreading"],
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
        # "production line 1 at EMEA-BCN" is not an identifier shape the
        # ontology resolves -- same family as using a factory's Code
        # where its FactoryId is the actual foreign key (see
        # ontology-shipments-to-factory below). Answers "no matching
        # ontology record" against a freshly refreshed graph.
        tag="known-to-fail",
        question="Which factory does production line 1 at EMEA-BCN belong to?",
        expect=["barcelona", "bcn", "emea"],
    ),
    dict(
        id="ontology-shipments-to-factory",
        tool="fabric_iq_ontology",
        tag="verified",
        # FAC-CHI is the factory's FactoryId (shipment.FromFactoryId's
        # actual foreign key) -- distinct from its Code, "NA-CHI" (see
        # factory.csv: FactoryId,Code are two different columns). Using
        # Code here returns zero results -- confirmed live, not a
        # system bug, a wrong identifier for this specific query.
        question="What shipments is factory FAC-CHI receiving?",
        expect=["ship-"],
    ),
    dict(
        id="centerpiece-shipments-and-quality",
        tool="fabric_iq_ontology + fabric_data_agent (chained)",
        # Both tools are still called, and the ontology half is correct,
        # but the chain does not close: the Data Agent declines to
        # filter quality checks by a factory list handed to it, because
        # shipments are outside its grounding scope ("the quality-check
        # tool cannot filter by shipment-receiving factories because it
        # has no shipments data"). Earlier runs looked like successes
        # partly because the count was 0, which is trivially
        # correlatable. Use centerpiece-worst-quality-and-inventory as
        # the flagship chain instead -- it resolves both halves.
        tag="known-to-fail",
        question=(
            "Which factories are receiving shipments, and how many "
            "quality checks failed today at those same factories?"
        ),
        expect=None,
        reject=["technical block", "couldn't get a valid", "blocked query"],
    ),
    dict(
        id="centerpiece-worst-quality-and-inventory",
        tool="fabric_data_agent + fabric_iq_ontology (chained)",
        # The flagship two-tool chain: the Data Agent returns the worst
        # factory this week, that factory becomes the ontology call's
        # argument, and the ontology returns its materials' on-hand
        # quantities against their reorder levels. Both halves resolve,
        # and the answer lands on a decision rather than a count.
        tag="verified",
        question=(
            "Which factory has the worst quality this week, and do we "
            "have enough inventory of its key material to keep it "
            "running?"
        ),
        expect=["fac-chi", "na-chi"],
        # A 200 whose second half quietly gives up would otherwise pass
        # the generic checks -- the inventory half is the point here.
        reject=["can't confirm", "cannot confirm", "couldn't confirm", "can't reliably"],
    ),
    dict(
        id="known-fail-pronoun-reference",
        tool="fabric_iq_ontology",
        # No referent for "this", and the failure is no longer an error:
        # the agent enumerates every supplier in the ontology as though
        # they all supplied the unnamed product, then offers to narrow
        # down. A plausible wrong answer passes every generic check a
        # tool error would trip, so it is matched explicitly via
        # `wrong_answer` below.
        tag="known-to-fail",
        question="Who are the suppliers of this product?",
        expect=None,
        wrong_answer=["product/material shown in the data", "if you meant a specific product"],
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
def graph_model_freshness():
    """Once per session: check the GraphModel's last refresh job status
    (see fabric/ontology/graph_refresh_status.py) so a stale/failed graph
    is a diagnosed, expected warning instead of an opaque 400 mid-run.
    There is still no Fabric REST API to trigger a refresh -- this only
    detects staleness, it can't fix it.
    """
    workspace_id = os.environ.get("FABRIC_WORKSPACE_ID")
    if not workspace_id:
        return None

    token = AzureCliCredential().get_token("https://api.fabric.microsoft.com/.default").token
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    job = get_last_refresh_job(session, workspace_id)

    if job is None or job.get("status") != "Succeeded":
        print(
            "\n"
            "=====================================================================\n"
            "WARNING: GraphModel's last refresh job did not succeed (or none was\n"
            f"found): {describe_last_refresh_job(job)}\n"
            "fabric_iq_ontology-dependent questions below may fail with \"The\n"
            "label expression (X) does not match any node type in the graph\" --\n"
            "open the Ontology item in the Fabric portal and run 'Refresh' before\n"
            "trusting these results as a measure of steady-state latency.\n"
            "====================================================================="
        )
        return describe_last_refresh_job(job)
    return None


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
    started_wall = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
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
    elapsed_seconds = time.monotonic() - started_monotonic
    return started_wall, elapsed_seconds, response


def _query_tool_call_durations(started_wall: datetime, elapsed_seconds: float) -> dict[str, float] | None:
    """Best-effort per-tool-call latency breakdown for one request, via
    App Insights `dependencies` (see SETUP.md's "Agent tracing and
    observability" -- confirmed live that tool calls show up there as
    per-call spans with real start/end timestamps). Correlated by a
    wall-clock time window around this request, not a request/trace id
    (none is available client-side from the Responses API response),
    so it's approximate under concurrent test runs -- fine for this
    harness's sequential `-s` usage, not a general-purpose profiler.

    Returns None (never raises) if AZURE_APP_INSIGHTS_NAME/
    AZURE_RESOURCE_GROUP aren't set, the `az` CLI call fails, or the
    dependency `name` field doesn't match a known tool label -- this is
    a diagnostic aid, never a test dependency. The exact `name` App
    Insights assigns to an MCP tool-call dependency hasn't been
    confirmed against a live trace at the time this was written; if the
    breakdown comes back empty, check the raw `az monitor app-insights
    query` output directly (SETUP.md has the exact command) and adjust
    the `has_any` list below.
    """
    if not APP_INSIGHTS_NAME or not APP_INSIGHTS_RESOURCE_GROUP:
        return None

    window_start = started_wall - timedelta(seconds=5)
    window_end = started_wall + timedelta(seconds=elapsed_seconds + 15)
    query = (
        "dependencies "
        f"| where timestamp between (datetime({window_start.isoformat()}) .. datetime({window_end.isoformat()})) "
        "| where name has_any ('fabric_iq_ontology', 'fabric_data_agent', 'knowledge_base') "
        "| project name, duration "
        "| order by timestamp asc"
    )
    try:
        proc = subprocess.run(
            [
                "az", "monitor", "app-insights", "query",
                "--app", APP_INSIGHTS_NAME,
                "--resource-group", APP_INSIGHTS_RESOURCE_GROUP,
                "--analytics-query", query,
                "--output", "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        rows = json.loads(proc.stdout)["tables"][0]["rows"]
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, IndexError) as e:
        print(f"  (App Insights per-hop query skipped: {e})")
        return None

    durations: dict[str, float] = {}
    for name, duration_ms in rows:
        durations[name] = durations.get(name, 0.0) + round(float(duration_ms) / 1000.0, 3)
    return durations or None


def _record(result: dict) -> None:
    with RESULTS_PATH.open("a") as f:
        f.write(json.dumps(result) + "\n")


@pytest.mark.parametrize("case", QUESTIONS, ids=[c["id"] for c in QUESTIONS])
def test_agent_question(agent_client, graph_model_freshness, case):
    session, project_endpoint = agent_client
    started_wall, elapsed_seconds, response = _ask(session, project_endpoint, case["question"])

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
        "tool_call_durations": _query_tool_call_durations(started_wall, elapsed_seconds),
        "tool_calls": tool_calls,
        "answer_text": text,
        "parse_error": parse_error,
        "raw_body_on_error": None if response.ok else body or response.text[:2000],
    }
    if "fabric_iq_ontology" in case["tool"] and graph_model_freshness:
        result["graph_stale_warning"] = graph_model_freshness
    _record(result)

    if case["tag"] == "known-to-fail":
        # Expected to fail today, per SETUP.md's diagnosed root causes.
        # xfail so it doesn't count as a broken test, but a surprise
        # pass is reported (strict=False) -- that would mean the repo's
        # docs are stale and should be updated.
        #
        # Some failures are answers, not errors: the agent returns a
        # confident, plausible, wrong result that trips none of the
        # markers below and passes every generic assertion. `wrong_answer`
        # names the phrasing that identifies one of those, so a silent
        # wrong answer is recorded as the documented failure it is
        # instead of a pass.
        wrong_answer = case.get("wrong_answer")
        if wrong_answer and any(kw in text.lower() for kw in wrong_answer):
            pytest.xfail(f"documented wrong-answer behavior reproduced: {text[:200]!r}")
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
