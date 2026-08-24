# Demo Questions

FabCon Europe 2026 · Barcelona · Question bank

Every question the live agent (`chocolate-factory-agent`) can be asked,
organized by which part of the architecture it exercises. Each is
marked:

- **✅ Verified** — run live against the deployed stack, exact result
  summarized. Safe to use as-is.
- **🧪 Candidate** — plausible given the real data model
  (`fabric/ontology/ontology_config.json`), not yet run live. Test
  before relying on it in front of a room.
- **❌ Known to fail** — run live and failed, with the diagnosed reason.
  Useful for the "known gaps" beat in `doc/fabcon-demo-scenario.md`,
  not for the main script.

See `.claude/plans/now-let-s-deploy-the-reactive-toucan.md` for the raw
test log these verifications come from.

## Foundry IQ knowledge base (`knowledge_base` tool)

Grounds in `foundry/kb/00-company-overview.md` through `03-erp-orders.md`.
Best for policy/definition questions a schema alone can't answer.

- ✅ *"What counts as an overdue invoice?"* — correct definition
  (past `DueDate`, no `PaidDate`), citation to `03-erp-orders.md`.
- ✅ *"What does CrystalFormIndex measure?"* — correct (a
  tempering-stage quality metric, target Form V), citation to
  `01-factory-quality.md`. Good because this metric exists **only** in
  the KB doc, not in any table schema — proves the KB earns its place.
- 🧪 *"Why can't a nib shortage always be substituted the way a
  packaging shortage can?"* — content exists in `02-supply-chain.md`;
  not yet run live.
- 🧪 *"What's our company's chocolate percentage range across
  recipes?"* — content exists in `00-company-overview.md`.

## Fabric Data Agent (`fabric_data_agent` tool)

Grounds in the Eventhouse (`silver_quality_check`, `silver_line_status`).
Best for live telemetry aggregates — this is the tool that proves the
demo is running against real, moving data, not a snapshot.

- ✅ *"How many quality checks failed today?"* — correctly reports the
  count (0 in the verified run, all passing).
- ✅ *"How is my factory going right now?"* — reports defect
  rate/pass-fail status correctly, and **explicitly says** it can't
  confirm live line-running status from available data rather than
  guessing. Worth narrating: that honesty is the point.
- ✅ *"Are there any anomalies I should be aware of?"* — the strongest
  single-tool answer verified so far: named downtime anomalies (50%+
  down-events on some lines), recurring reasons (changeover, scheduled
  maintenance, unplanned stops), and a specific outlier called out by
  name ("Molding & Cooling" fail-rate spike on LATAM-GRU Line 2).
- 🧪 *"Which line has the most downtime today?"*
- 🧪 *"What's the average defect rate by stage this week?"*

## Fabric IQ Ontology (`fabric_iq_ontology` tool)

Grounds in the 22-entity, 27-relationship ontology spanning
Factory/Quality and Supply Chain/ERP. Best for structural/relationship
questions — this is the "semantic layer as a real queryable graph, not
a diagram" beat.

- ✅ *"What entity types exist in the ontology?"* — correctly
  enumerates all 22 across both domains.
- ✅ *"Who are our suppliers?"* — correctly lists all 9 real suppliers
  by name.
- ✅ *"Which suppliers provide materials, and what type of material
  does each provide?"* — correct supplier↔material-type pairings for
  all 9.
- 🧪 *"Which factory does production line X belong to?"* (`line_to_factory`
  relationship, real data).
- 🧪 *"What shipments is factory FAC-CHI receiving?"* (`shipment_to_factory`,
  real data — this is half of the centerpiece question below, worth
  testing standalone too).

## The centerpiece — cross-domain reasoning (2 tools, chained)

The actual evidence that one agent can reason across specialized
backends, not a claim. Verified live: the second tool call's argument
depends on the first call's result.

- ✅ *"Which factories are receiving shipments, and how many quality
  checks failed today at those same factories?"* — calls
  `fabric_iq_ontology` first (finds a factory receiving a shipment via
  `shipment_to_factory`), then calls `fabric_data_agent` **using that
  specific factory as input**, then synthesizes both into one
  correlated answer. If the demo surface can show the raw response
  JSON, the `mcp_call` entries (`server_label`, `arguments`) are worth
  projecting.
- 🧪 *"Which factory has the worst quality this week, and do we have
  enough inventory of its key material to keep it running?"` — a more
  ambitious version of the same pattern, spanning all 3 tools/domains.
  Not yet tested; likely to hit the known material/recipe gap below if
  phrased around a specific product rather than a factory. Test before
  using live.

## Known to fail — useful for the honesty beat, not the main script

- ❌ *"Who are the suppliers of **this** product?"* — fails with
  `search_ontology: query could not be processed`. Root cause,
  isolated by retesting with clearer phrasing: **"this product" has no
  referent in a single fresh turn** — not a data gap. Rephrasing to
  "our suppliers" or a named recipe works fine (see ✅ above).
- ❌ (by data-model inspection, not yet run) *"Which suppliers fed the
  batches in Dark 70% production?"* or similar batch-to-material
  traceability — no `recipe`/`batch` → `material`/`supplier`
  relationship exists in the ontology at all. Real gap, not a bug: the
  data to support batch-level material genealogy was never generated
  on either the simulator or seed-data side. See
  `fabric/ontology/generate_fabric_iq_definition.py`'s docstring and
  `doc/fabcon-demo-scenario.md` §6 for how to talk about this if asked.
- ❌ (by design, not yet run) anything requiring `batch_to_line` or
  `batch_to_recipe` relationship data — both are type-only (no real
  instance data), because their source `silver_batch` is a
  materialized view and OneLake mirroring doesn't support materialized
  views yet.

## Multi-agent architecture (A2A) — not currently demoable

No live questions here: the Coordinator + 2-specialist-agent
architecture was built and torn down in a timeboxed spike (2026-08-24)
after confirming the underlying A2A protocol works but the product's
`a2a_preview` tool invocation path doesn't, reproducibly. See
`doc/fabcon-demo-scenario.md` §5 for how to present this honestly —
the centerpiece question above is the actual evidence for "multi-agent
reasoning" the talk can show live today.
