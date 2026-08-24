# FabCon Demo Scenario

FabCon Europe 2026 · Barcelona · Demo scenario

End-to-end walkthrough for "Multi-Agent Data Systems: Fabric + Foundry
in Action" (60 min, level 300, with Dian Kang). This is the script —
what we actually run, in what order, and why — cross-referenced against
the code and infra it's built on. Nothing here is aspirational; every
question and answer in §3 was run live against the deployed stack on
2026-08-24 (see `.claude/plans/now-let-s-deploy-the-reactive-toucan.md`
for the raw test log).

## 1. What this demo is actually showing

Four learning objectives, and where each one lives in the stack:

| Objective | Where it lives |
|---|---|
| Fabric grounding enterprise data | Eventhouse (live telemetry) + Fabric SQL Database (Supply Chain/ERP) + Foundry IQ knowledge base, all queried live, not mocked |
| Fabric IQ for context-aware reasoning | The Fabric IQ Ontology — 22 entity types, 27 real bound relationships spanning both engines |
| Orchestrate AI workflows using Foundry | One Foundry Agent Service agent, 3 tools, picking the right one per question and citing it |
| Multi-agent architectures combining data and AI agents | Framed honestly in §5 — this is where the talk earns its "in Action" credibility rather than a polished happy path |

## 2. Architecture, as built

```
simulator  --Event Hub-->  Eventstream  -->  Eventhouse (Bronze/Silver/Gold KQL)
                                                    |
                                        OneLake mirroring + shortcuts
                                                    v
Fabric SQL Database (Supply Chain/ERP) ----> Fabric IQ Ontology (22 entities, 27 relationships)
                                                    |
foundry/kb/*.md --> Azure AI Search --> Foundry IQ knowledge base
                                                    |
                                                    v
                              chocolate-factory-agent (Foundry Agent Service)
                               tools: knowledge_base | fabric_data_agent | fabric_iq_ontology
```

One `terraform apply` (`infra/`) provisions every box above, including
the agent and all 3 tool connections — see `infra/README.md`'s
"Reproducing on a different subscription" section for the two things
that stay genuinely manual (Fabric IQ region availability; whoever
*queries* the agent needs their own real Fabric workspace role, since
two of the three tools use `UserEntraToken` auth — more on why in §4).

## 3. The live scenario — run in order

**Setup, ~2 minutes before going live:**

```bash
cd simulator
uv run --env-file .env run_simulator.py --interval 5 --anomaly-rate 0.03 --downtime-rate 0.015 --max-runtime 120
```

Streams live telemetry for all 4 factories (EMEA-BCN, NA-CHI,
LATAM-GRU, APAC-SIN) into the Eventhouse. Verified live: a 2-minute run
streams ~700-750 events with no connection errors. Let it run in a
terminal on screen — the room should see numbers moving before any
slide changes.

**Then, against the live agent** (`chocolate-factory-agent`, queried
via `foundry/agents/README.md`'s REST pattern or the Foundry portal
playground):

1. *"What counts as an overdue invoice?"* — `knowledge_base` tool,
   answers with a citation back to `foundry/kb/03-erp-orders.md`. Shows
   retrieval grounding, not hallucination.
2. *"How is my factory going right now?"* — `fabric_data_agent` tool.
   Verified live: reports defect rate and pass/fail status correctly,
   and — importantly — says plainly when it can't confirm something
   (live line-running status) rather than guessing. Worth narrating:
   *this* honesty is a feature, not a gap.
3. *"Are there any anomalies I should be aware of?"* — `fabric_data_agent`
   tool. This is the strongest single-tool answer verified so far:
   named downtime anomalies, recurring reasons (changeover, scheduled
   maintenance, unplanned stops), and a specific outlier called out by
   name ("Molding & Cooling" on LATAM-GRU Line 2). Use this as the
   "look how specific this is" beat.
4. *"Who are our suppliers?"* then *"Which suppliers provide materials,
   and what type of material does each provide?"* — `fabric_iq_ontology`
   tool, both verified correct against all 9 real suppliers. Shows the
   Ontology as a genuine queryable graph, not a diagram.
5. **The centerpiece:** *"Which factories are receiving shipments, and
   how many quality checks failed today at those same factories?"* —
   the agent calls `fabric_iq_ontology` first (finds a factory
   receiving a shipment via the `shipment_to_factory` relationship),
   then calls `fabric_data_agent` **using that specific factory as
   input**, then synthesizes both into one correlated answer. This is
   the actual evidence — not a claim — that one agent can chain
   reasoning across two specialized backends, one feeding the other.
   If the on-screen client can show the raw response JSON, the
   `mcp_call` entries with `server_label`/`arguments` are worth
   projecting for a level-300 room.

**One phrasing rule, learned the hard way:** don't ask pronoun-referencing
questions cold (*"who are the suppliers of **this** product?"*) — verified
live to fail, because "this product" has no referent in a single fresh
turn and the model passes the literal ambiguous phrase into the
ontology query. Use a concrete noun instead ("our suppliers," a named
recipe) unless the demo deliberately builds up context over several
turns first.

## 4. The auth story, if the room asks "how does this actually work"

Worth having ready, not necessarily scripted: getting the 3 tools
working needed real debugging, documented in
`foundry/agents/deploy_foundry_agent.py`'s docstring and
`foundry/agents/README.md`. Short version: `knowledge_base` uses
`ProjectManagedIdentity` (service-to-service, fine for a knowledge
base); the two Fabric tools needed `UserEntraToken` instead, because
both the Fabric Data Agent and the Ontology's MCP endpoint reject
non-interactive identities for actual query execution — confirmed live
with two independent service-principal tokens before finding the fix
that mirrors Microsoft's own one documented Data Agent REST example.

## 5. "What we tried and why it didn't make the cut" — the transparency beat

The section that makes this a level-300 talk instead of a vendor demo.
Present both as evidence of rigor, not confessions — the room should
leave trusting the parts that *do* work more, not less.

**A second Data Agent source for Supply Chain/ERP — abandoned.** Tried
5 configurations grounding the shared Fabric Data Agent in a Lakehouse
mirror of the Supply Chain/ERP tables, escalating each time, including
an exact byte-for-byte reproduction of a config the Fabric portal's own
"+ Add data source" picker generated. All 5 failed identically — the
decisive one was tested in **the portal's own native chat panel**, not
just our code, which is what rules out a config mistake and confirms a
genuine current platform limitation in Data Agent + Lakehouse Tables
query execution for this data shape. Resolution: Supply Chain/ERP
grounding comes from the Ontology tool instead — no functionality
lost, just routed differently. Full sequence in `foundry/agents/README.md`.

**A2A agent-to-agent orchestration — abandoned, with a fully-diagnosed
root cause.** A timeboxed spike (Aug 24) to build a real Coordinator +
2 specialist-agent architecture, specifically to serve the "multi-agent
architectures" objective more literally than one agent with three
tools does. What actually happened, worth telling honestly:

- Found and fixed two genuinely undocumented requirements: the calling
  agent's own instance identity needs its own "Foundry Agent Consumer"
  role grant, separate from the project's identity (not in any doc);
  and the A2A JSON-RPC message schema requires `kind` discriminators
  (`message.kind: "message"`, `parts[].kind: "text"`) that Microsoft's
  own published examples omit entirely.
- Got the raw A2A protocol working end to end — a direct JSON-RPC call
  to the target agent's endpoint returned the correct, verified answer.
- The actual product mechanism for wiring this into a live agent
  (`a2a_preview` tool, tried both via a toolbox and attached directly)
  failed reproducibly with an opaque, non-diagnosable error — confirmed
  not to be a config mistake by standing up Application Insights
  specifically to chase it, which additionally revealed that
  Prompt-kind Foundry agents don't get platform execution tracing the
  way Hosted agents do.
- Full trail, including every JSON body tried and every error message
  hit, in `.claude/plans/now-let-s-deploy-the-reactive-toucan.md`.

**The honest framing for the room:** "multi-agent" in production today
most often means one orchestrating agent reasoning across multiple
*specialized data agents and knowledge sources* — not necessarily
multiple *conversational* agents talking to each other. §3's centerpiece
question is real evidence that pattern works well right now. We also
tried the more literal interpretation via Foundry's new `a2a_preview`
tool, and can show exactly where that stands today: the protocol and
auth model work, the product's own tool-invocation layer for it
doesn't yet — which is a more credible, more memorable talk for this
audience than pretending everything works everywhere.

## 6. Known, documented gaps (say these plainly if asked, don't dodge)

- `batch_to_line` / `batch_to_recipe` relationships are type-only, not
  bound with real instance data — `silver_batch` is a materialized
  view, and OneLake mirroring doesn't support materialized views yet.
  See `fabric/ontology/generate_fabric_iq_definition.py`'s docstring.
- No `recipe`/`batch` → `material`/`supplier` relationship exists at
  all — the ontology can answer "who are our suppliers" but not
  genuine lot-level traceability ("which supplier fed this specific
  batch"). A real gap, not a config issue: the data to support it was
  never generated on either the simulator or seed-data side.
- Two things stay genuinely manual on a fresh deploy: Fabric IQ's
  region availability, and Fabric workspace access for whoever isn't
  the person who ran `terraform apply` (see `infra/README.md`).

## 7. Reproducibility, if it comes up

Everything in §2 is one `terraform apply` against any subscription
with an existing resource group — checked directly: no hardcoded
subscription/tenant IDs anywhere in `.tf`. Worth a slide, not a live
demo (re-provisioning live is unnecessary risk for a 60-minute slot).
