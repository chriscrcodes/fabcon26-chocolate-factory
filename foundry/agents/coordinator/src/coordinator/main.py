# Copyright (c) Microsoft. All rights reserved.
"""Coordinator for the FabCon chocolate factory demo.

This is a Hosted (code-defined) Foundry agent -- deliberately not a
Prompt agent -- specifically so it can call each specialist's A2A
endpoint directly with plain HTTP from its own container, sidestepping
Foundry's built-in `a2a_preview` tool. That tool was spiked twice
(toolbox-wired and direct-attached) and failed reproducibly in both
wirings; see ../../README.md and the two plan files it links
(`~/.claude/plans/now-let-s-deploy-the-reactive-toucan.md` for the
NO-GO findings, `~/.claude/plans/please-analyze-current-project-majestic-meteor.md`
for why a Hosted Coordinator was tried next) for the full investigation
history. The raw A2A JSON-RPC protocol against a target agent's
endpoint, by contrast, is proven live and reused verbatim here.

Routing design (documented per the plan's request to state the choice
and why): this Coordinator uses a **deterministic keyword heuristic**
(`route_question` below), not a separate LLM classification call and
not the hosted model's own tool-selection judgment. The two specialist
domains are lexically distinct enough (Factory/Quality telemetry
vocabulary vs. Supply Chain/ERP commercial vocabulary -- see
doc/cacao-data-model.md's per-specialist "Owns" column) that a keyword
match is enough for a demo router, and it keeps the routing decision
fully deterministic and visible in the trace rather than hidden inside
another model call. The hosted model itself is used only as a
single-tool passthrough: `answer_question` is the only tool, and the
instructions require the model to call it with the user's literal
question and relay its result verbatim -- so what the audience/trace
sees is exactly one LLM turn plus the direct A2A hop(s) this file
performs, not extra hidden model reasoning about which specialist to
pick.

Cross-domain handling (the plan's acceptance-test shape: "Which
factories are receiving shipments, and how many quality checks failed
today at those same factories?"): Supply Chain/ERP is called first
(per the plan's own ordering, mirroring the generalist agent's proven
internal tool-chaining -- Ontology before Data Agent), its raw answer
text is scanned for a known factory code/city name (simple substring
match against the 4 factories in doc/cacao-data-model.md -- no second
LLM call needed for this extraction), and if one is found it's folded
into the question sent to the Factory/Quality specialist as plain
context text. This is intentionally simple string handling, not a
second classification pass -- matches the plan's "ask specialist A,
then include the raw specialist A answer as context in the question
sent to specialist B" guidance.
"""

import asyncio
import os
import re
import uuid

import httpx
from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from pydantic import Field
from typing_extensions import Annotated

# Load environment variables from .env file
load_dotenv()

# Specialist agent names -- must match deploy_foundry_agent.py's
# AGENT_NAME derivation for the "specialist_factory_quality" and
# "specialist_supply_chain_erp" profiles (../../deploy_foundry_agent.py).
FACTORY_QUALITY_AGENT_NAME = "chocolate-factory-specialist-factory-quality"
SUPPLY_CHAIN_ERP_AGENT_NAME = "chocolate-factory-specialist-supply-chain-erp"

# A2A calls use the ai.azure.com scope against the project's own agent
# endpoints, distinct from the AZURE_AI_MODEL_DEPLOYMENT_NAME chat-completion
# scope FoundryChatClient uses below (see ../../README.md's note on the two
# different token scopes involved in this repo's agent spine).
A2A_TOKEN_SCOPE = "https://ai.azure.com/.default"

# Keyword sets for the deterministic router -- see module docstring.
# Vocabulary drawn from doc/cacao-data-model.md's per-domain "Owns" tables,
# not invented: production lines/batches/sensors/quality for Factory/Quality,
# suppliers/materials/inventory/shipments/customers/orders/invoices for
# Supply Chain/ERP.
_FACTORY_QUALITY_KEYWORDS = (
    "quality", "defect", "yield", "line", "production line", "downtime",
    "anomaly", "anomalies", "sensor", "telemetry", "batch", "running",
    "down", "maintenance", "changeover", "factory going", "line status",
    "temper", "mold", "conching", "grinding", "refining",
)
_SUPPLY_CHAIN_ERP_KEYWORDS = (
    "supplier", "material", "inventory", "shipment", "shipping", "carrier",
    "customer", "order", "invoice", "overdue", "payment", "credit limit",
    "product", "sku", "warehouse", "reorder", "stock",
)

# Known factory codes/cities (doc/cacao-data-model.md §1) -- used only to
# extract a factory reference out of a specialist's free-text answer, no
# second LLM call needed for this.
_FACTORY_REFERENCES = (
    "EMEA-BCN", "Barcelona",
    "NA-CHI", "Chicago",
    "LATAM-GRU", "São Paulo", "Sao Paulo",
    "APAC-SIN", "Singapore",
)


def route_question(question: str) -> tuple[bool, bool]:
    """Deterministic keyword routing. Returns (needs_factory_quality, needs_supply_chain)."""
    lowered = question.lower()
    needs_fq = any(kw in lowered for kw in _FACTORY_QUALITY_KEYWORDS)
    needs_sc = any(kw in lowered for kw in _SUPPLY_CHAIN_ERP_KEYWORDS)
    if not needs_fq and not needs_sc:
        # Ambiguous/ungrounded question -- fall back to asking both rather
        # than guessing which single specialist to skip. Simple and safe
        # for a demo; a production router would do better here.
        return True, True
    return needs_fq, needs_sc


def extract_factory_reference(text: str) -> str | None:
    """Pull a known factory code/city out of a specialist's answer text."""
    for reference in _FACTORY_REFERENCES:
        if re.search(re.escape(reference), text, flags=re.IGNORECASE):
            return reference
    return None


async def _call_specialist_a2a(agent_name: str, question: str) -> str:
    """Call one specialist agent over its A2A endpoint.

    Confirmed live shape (see ../../README.md and the Phase-1 spike
    notes it links): `message/send` is asynchronous -- it returns
    immediately with `result.status.state: "submitted"` and a task
    `id`, NOT the answer. The answer only appears once `tasks/get`
    reports `status.state == "completed"`, under
    `result.artifacts[].parts[].text`. Both `message.kind: "message"`
    and `parts[].kind: "text"` are required discriminators the A2A
    docs' own examples omit -- without them the target rejects the
    call before ever reaching agent resolution.
    """
    account = os.environ["AZURE_FOUNDRY_ACCOUNT_NAME"]
    project = os.environ["AZURE_FOUNDRY_PROJECT_NAME"]
    project_endpoint = f"https://{account}.services.ai.azure.com/api/projects/{project}"
    a2a_url = f"{project_endpoint}/agents/{agent_name}/endpoint/protocols/a2a"

    credential = DefaultAzureCredential()
    token = credential.get_token(A2A_TOKEN_SCOPE).token
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        send_body = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "messageId": str(uuid.uuid4()),
                    "role": "user",
                    "parts": [{"kind": "text", "text": question}],
                }
            },
        }
        resp = await client.post(a2a_url, headers=headers, json=send_body)
        resp.raise_for_status()
        send_result = resp.json()
        task_id = send_result.get("result", {}).get("status", {}).get("id") or send_result.get("result", {}).get("id")
        if task_id is None:
            raise RuntimeError(f"{agent_name}: message/send did not return a task id: {send_result}")

        # Poll tasks/get until completed. A fixed short interval with a
        # generous cap is enough for a demo; no exponential backoff needed
        # at this scale (single-question, single-user requests).
        for _ in range(60):
            get_body = {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "tasks/get",
                "params": {"id": task_id},
            }
            resp = await client.post(a2a_url, headers=headers, json=get_body)
            resp.raise_for_status()
            get_result = resp.json().get("result", {})
            state = get_result.get("status", {}).get("state")
            if state == "completed":
                texts = [
                    part.get("text", "")
                    for artifact in get_result.get("artifacts", [])
                    for part in artifact.get("parts", [])
                    if "text" in part
                ]
                return "\n".join(texts).strip() or "(specialist returned no text)"
            if state in ("failed", "canceled", "rejected"):
                raise RuntimeError(f"{agent_name}: task {task_id} ended in state {state!r}: {get_result}")
            await asyncio.sleep(1.0)

    raise TimeoutError(f"{agent_name}: task {task_id} did not complete in time")


async def answer_question_impl(question: str) -> str:
    needs_factory_quality, needs_supply_chain = route_question(question)

    answers: list[str] = []

    if needs_supply_chain:
        # Supply Chain/ERP goes first when both are needed -- mirrors the
        # generalist agent's own proven internal chaining order (Ontology
        # before Data Agent) for the cross-domain acceptance question.
        supply_chain_answer = await _call_specialist_a2a(SUPPLY_CHAIN_ERP_AGENT_NAME, question)
        answers.append(f"[Supply Chain/ERP specialist]\n{supply_chain_answer}")

        if needs_factory_quality:
            factory_reference = extract_factory_reference(supply_chain_answer)
            followup = question
            if factory_reference:
                followup = (
                    f"{question}\n\n"
                    f"Context from the Supply Chain/ERP specialist: the relevant "
                    f"factory is {factory_reference}. Focus your answer on that factory."
                )
            factory_quality_answer = await _call_specialist_a2a(FACTORY_QUALITY_AGENT_NAME, followup)
            answers.append(f"[Factory/Quality specialist]\n{factory_quality_answer}")

    elif needs_factory_quality:
        factory_quality_answer = await _call_specialist_a2a(FACTORY_QUALITY_AGENT_NAME, question)
        answers.append(f"[Factory/Quality specialist]\n{factory_quality_answer}")

    return "\n\n".join(answers)


@tool(approval_mode="never_require")
async def answer_question(
    question: Annotated[str, Field(description="The user's question, passed through verbatim.")],
) -> str:
    """Route the question to the Factory/Quality and/or Supply Chain/ERP
    specialist(s) over direct A2A calls, and return their answer(s)."""
    return await answer_question_impl(question)


def main():
    model_name = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME") or os.getenv("FOUNDRY_MODEL_NAME")
    if not model_name:
        raise RuntimeError(
            "Model deployment name is not configured. Set "
            "AZURE_AI_MODEL_DEPLOYMENT_NAME or FOUNDRY_MODEL_NAME."
        )

    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=model_name,
        credential=DefaultAzureCredential(),
    )

    agent = Agent(
        client=client,
        instructions=(
            "You are the chocolate factory demo's Coordinator. You have "
            "exactly one tool, answer_question -- always call it with the "
            "user's question passed through verbatim (do not paraphrase or "
            "add context yourself), and return its result to the user "
            "essentially verbatim (light formatting only). Do not attempt "
            "to answer from your own knowledge: all factual answers come "
            "from the tool, which routes to the domain specialist(s)."
        ),
        tools=[answer_question],
        # History will be managed by the hosting infrastructure, thus there
        # is no need to store history by the service. Learn more at:
        # https://developers.openai.com/api/reference/resources/responses/methods/create
        default_options={"store": False},
    )

    server = ResponsesHostServer(agent)
    server.run()


if __name__ == "__main__":
    main()
