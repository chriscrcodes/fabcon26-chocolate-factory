# Instructions

End-to-end steps to deploy this stack and run the demo: infra, the
simulator, and the live agent. For *what* this demo is and *why* it's
built this way, see [`README.md`](README.md). For the full FabCon
talk script (narrative framing, talking points, the honest
"what didn't work" section), see
[`doc/fabcon-demo-scenario.md`](doc/fabcon-demo-scenario.md).

## 1. Prerequisites

Beyond filling in `infra/terraform.tfvars`, the deploying identity and
machine need the following. Each of these tends to surface only
partway through `terraform apply` (earlier resources create fine
before the failure), so confirm them up front:

- **A Fabric-licensed identity recognized by the Connections API.**
  Must be able to create Fabric Connections, not just browse the
  Fabric portal — general workspace access (Pro/PPU license alone) is
  not always sufficient. Use a dedicated Fabric administrator account
  if `terraform plan` fails on `fabric_connection.event_hub` with
  `UserNotLicensed`.
- **Fabric capacity admins all in one tenant** —
  `fabric_capacity_admin_members` entries must share the deploying
  identity's AAD tenant.
- **RBAC: Owner, or Contributor + User Access Administrator, on the
  target resource group** — several `azurerm_role_assignment`
  resources need role-assignment write access.
- **Azure OpenAI quota for the deployed model/SKU in the target
  region**, confirmed before applying (`az cognitiveservices usage
  list --location <region>`).
- **Fabric IQ (Ontology) enabled at the tenant level** — a separate
  Fabric Admin Portal tenant setting from general Fabric licensing
  above.
- **A CA bundle trusting your org's TLS-inspecting proxy, if any**
  (e.g. Zscaler) — several `local-exec` provisioners call Fabric/Kusto
  endpoints directly via Python's `requests`.
- **[`uv`](https://docs.astral.sh/uv/)** — both the Terraform
  `local-exec` provisioners and the simulator shell out through it.

Full detail and exact error messages for each of these:
[`infra/README.md`](infra/README.md#prerequisites).

## 2. Deploy the infrastructure

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # fill in your Azure/Fabric values
terraform init
terraform apply
```

One `terraform apply` against any subscription, into any existing (or
new, with `create_resource_group = true`) resource group, provisions
everything: Fabric capacity, workspace, Eventhouse, Ontology, SQL
Database, the Foundry IQ knowledge base, the Foundry project, and the
agent itself, wired to all its tools. Nothing manual is left after it
completes except the one-time Operations Agent portal step in §5.

If the Fabric capacity was paused (nightly auto-pause is on by
default), resume it before planning/applying — a paused capacity
makes `terraform plan` misread existing Fabric items as needing
re-creation:

```bash
uv run fabric/manage_capacity.py resume
```

Full technical detail on every resource this provisions:
[`infra/README.md`](infra/README.md).

## 3. Run the simulator

### One-time setup

```bash
cd simulator
uv sync
cp .env.sample .env   # fill in your Event Hub connection string or namespace
```

`AZURE_EVENT_HUB_NAMESPACE_HOSTNAME` and `AZURE_EVENT_HUB_NAME` come
from `infra`'s `AZURE_EVENT_HUB_NAMESPACE_HOSTNAME`/`AZURE_EVENT_HUB_NAME`
outputs (`terraform output`) if you're using `az login` auth instead of
a connection string — the deploying identity already has **Azure Event
Hubs Data Sender** on that Event Hub from the deploy.

Dimension CSVs (`fabric/ontology/tables/*.csv` — factories, lines,
recipes) are committed in the repo and already loaded into Fabric by
`terraform apply`; only re-run `uv run run_seed_data.py` if you want to
regenerate them.

### Real-time streaming — baseline demo run

```bash
uv run --env-file .env run_simulator.py --interval 5 --anomaly-rate 0.03 --downtime-rate 0.015
```

Streams live telemetry for all 4 factories. Verified live: ~700-750
events over 2 minutes with no connection errors. Add `--max-runtime
120` to auto-stop, or Ctrl+C. Start this ~2 minutes before going live
— let the room see numbers moving before any slide changes.

### Backfilling history

A fresh environment has no multi-day history, which the Gold layer's
daily-bucketed views (`gold_defect_rate_by_stage_daily`,
`gold_factory_oee_daily`) need. Run once, before rehearsing queries
that depend on trends:

```bash
uv run --env-file .env run_simulator.py --backfill-hours 48 --interval 30
```

Virtual clock, no wall-clock sleep — 48h at 30s/tick is 5760 ticks,
sent in batches of 20 (`--backfill-batch-ticks`) to avoid Event Hub
throttling.

### Scenario variants

The simulator has no per-scenario flag — anomaly/downtime are per-tick
probabilities applied uniformly to every line. Dial the rate up for a
short bounded run right before you need a specific demo beat:

| Scenario | Command | What it shows |
|---|---|---|
| **Normal** | `--anomaly-rate 0.03 --downtime-rate 0.015` | Baseline — realistic occasional blips, most quality checks pass |
| **Quality anomaly burst** | `--anomaly-rate 0.5 --max-runtime 60` | Sensor values pushed outside their normal range, driving `DefectRate` up and `quality_check.Result = "Fail"` — good for *"are there any anomalies I should be aware of?"* |
| **Line downtime** | `--downtime-rate 0.3 --max-runtime 60` | Lines flip to `Down` with a reason (Scheduled Maintenance / Unplanned Stop / Changeover), stop emitting readings |
| **Tempering / Operations Agent trigger** | `--anomaly-rate 1.0`, let it run a full batch cycle | Guarantees a `CrystalFormIndex` reading outside its normal range during the Tempering stage — what `fabric_operations_agent.predictive_maintenance` watches for its Form V drift alert. Requires the §5 manual portal step to actually fire. |

At `--anomaly-rate 1.0`, a line takes a full cycle (26 ticks across all
6 stages) to reach Tempering — let it run at least that long rather
than a very short `--max-runtime`.

## 4. Query the demo agent

```bash
TOKEN=$(az account get-access-token --scope https://ai.azure.com/.default --query accessToken -o tsv)
curl -s "<AZURE_FOUNDRY_ACCOUNT_ENDPOINT>/api/projects/chocolate-factory/openai/v1/responses" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"agent_reference": {"type": "agent_reference", "name": "chocolate-factory-agent"}, "input": [{"role": "user", "content": "<your question>"}]}'
```

`<AZURE_FOUNDRY_ACCOUNT_ENDPOINT>` is a `terraform output` from `infra`.
Or use the Foundry portal playground directly. Either way, whoever
queries the agent needs their own real Fabric workspace role — not
necessarily the same identity that ran `terraform apply` (see
[`infra/README.md`](infra/README.md)).

The validated walkthrough, run in order:

1. *"What counts as an overdue invoice?"* — `knowledge_base` tool,
   answers with a citation.
2. *"How is my factory going right now?"* — `fabric_data_agent` tool;
   reports defect rate and pass/fail status, and says plainly when it
   can't confirm something rather than guessing.
3. *"Are there any anomalies I should be aware of?"* — `fabric_data_agent`
   tool; names specific downtime anomalies and outliers.
4. *"Who are our suppliers?"* then *"Which suppliers provide
   materials, and what type of material does each provide?"* —
   `fabric_iq_ontology` tool.
5. **The centerpiece:** *"Which factories are receiving shipments, and
   how many quality checks failed today at those same factories?"* —
   the agent chains `fabric_iq_ontology` (finds the factory via
   `shipment_to_factory`) into `fabric_data_agent` (using that factory
   as input), then synthesizes both.

Don't ask pronoun-referencing questions cold (*"who are the suppliers
of **this** product?"*) — verified live to fail, since "this product"
has no referent in a fresh turn. Use a concrete noun instead.

The full question bank, each tagged by what actually happened when run
live: [`doc/questions.md`](doc/questions.md).

## 5. One-time manual step: Operations Agent

`fabric_operations_agent.predictive_maintenance` can't be fully wired
up through Terraform or the REST API — the alert action's Power
Automate connection is portal/maker-only. After `terraform apply`,
finish this once by hand:
[`doc/operations-agent-setup.md`](doc/operations-agent-setup.md).

## 6. Pausing when done

A paid Fabric capacity bills per-minute while `Active`. Nightly
auto-pause is on by default (`fabric_capacity_auto_pause_enabled`,
20:00 UTC), or pause manually when you're done for the day:

```bash
uv run fabric/manage_capacity.py pause
```
