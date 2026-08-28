# infra

Azure-side IaC for the chocolate factory demo, plus what's realistically
IaC-able on the Fabric side today (research below — nothing there is
scripted yet).

## What's here

One Terraform state (not Bicep — see "Why Terraform" below), Azure and
Fabric together:

- [`providers.tf`](providers.tf) — `azurerm`, `microsoft/fabric`, `azapi`,
  and `time` providers, all defaulting to Azure CLI auth (`az login`), no
  separate setup
- [`azure.tf`](azure.tf) — variables, resources, and outputs for the
  Azure Event Hub Namespace + Event Hub + role assignments (that
  `simulator` streams to and `fabric.tf`'s Connection reads from), the
  Azure AI Search service backing the Foundry IQ knowledge base, and a
  Microsoft Foundry project (`azurerm_cognitive_account` with
  `kind = "AIServices"` + `azurerm_cognitive_account_project` — the
  modern project-only shape, no Key Vault/Storage Account/Hub
  dependency like the older `azurerm_ai_foundry`/`azurerm_ai_foundry_project`
  pair, which provisions the legacy hub-based architecture that Foundry
  IQ knowledge sources explicitly don't support) for the Phase 3 agent
  spine (`foundry/agents/`) -- including the 3 `azapi_resource` project
  connections (knowledge base, Fabric Data Agent, Fabric IQ Ontology)
  and the `null_resource` that deploys the actual Foundry Agent Service
  agent itself (`foundry/agents/deploy_foundry_agent.py`) wired to all
  3 as tools
- [`fabric.tf`](fabric.tf) — variables, resources, and outputs for the
  whole Fabric side: the capacity itself (`Microsoft.Fabric/capacities`,
  F2 by default, configurable via `fabric_capacity_sku`, provisioned via
  the `azapi` provider since neither `azurerm` nor `microsoft/fabric`
  expose that ARM resource), a dedicated workspace on it, Eventhouse, KQL
  Database, the Fabric Connection to the Event Hub, the Eventstream item
  itself (built from `fabric/eventhouse/eventstream.json`), a Lakehouse
  holding the ontology's dimension tables, a mirror of the Supply
  Chain/ERP tables (for Ontology binding — see below), *and* the
  `foundry/kb/*.md` knowledge-base docs, a Fabric SQL Database holding
  the authoritative Supply Chain/ERP tables, a Fabric IQ Ontology
  (preview) bound across the Eventhouse and Lakehouse, a Fabric Data
  Agent grounded in the Eventhouse — Factory/Quality only; a second
  Lakehouse-backed source for Supply Chain/ERP was tried and abandoned,
  see `fabric/data-agent/README.md` — a workspace role assignment granting
  the
  Azure AI Search service (`azure.tf`) Contributor so its OneLake
  indexer can read the Lakehouse, and `null_resource`s (`hashicorp/null`
  provider) that deploy the Bronze/Silver/Gold KQL, the dimension
  Lakehouse tables, the Supply Chain/ERP SQL schema + seed data, the
  Ontology definition, the KB markdown uploads, and the Search
  data-source/index/indexer, all via `local-exec` provisioners — see
  [`../fabric/eventhouse/run_kql.py`](../fabric/eventhouse/run_kql.py),
  [`../fabric/ontology/deploy_dimension_lakehouse.py`](../fabric/ontology/deploy_dimension_lakehouse.py),
  [`../fabric/sql-database/deploy_sql_database.py`](../fabric/sql-database/deploy_sql_database.py),
  [`../fabric/ontology/deploy_fabric_iq_ontology.py`](../fabric/ontology/deploy_fabric_iq_ontology.py),
  [`../foundry/kb/deploy_kb_files.py`](../foundry/kb/deploy_kb_files.py), and
  [`../foundry/kb/deploy_search_indexer.py`](../foundry/kb/deploy_search_indexer.py)
- [`terraform.tfvars.example`](terraform.tfvars.example) — copy to
  `terraform.tfvars` and fill in the Azure/Fabric values

Deployed and verified end to end against a real tenant: `terraform apply`
provisions the F2 capacity, workspace, Eventhouse, KQL database,
Connection, Eventstream, Bronze/Silver/Gold KQL + reference data,
dimension Lakehouse, Fabric SQL Database + Supply Chain/ERP data,
Fabric IQ Ontology, the Foundry IQ knowledge base, the Foundry project
and model deployment, all 3 agent tool connections, and the Foundry
Agent Service agent itself, wired to all 3 tools -- and `simulator`
streams events all the way through to the Gold layer. One
`terraform apply` against an existing resource group in any
subscription/tenant reproduces the whole stack; see "Reproducing on a
different subscription" below for the couple of things that are
genuinely outside Terraform's reach.

Event Hub auth, chosen by whether a workspace identity is available
(`workspace_identity_principal_id`, resolved automatically from
`fabric.tf` when `enable_workspace_identity = true` — see below):

- **Default — SAS, local auth enabled.** The deploying user gets
  **Azure Event Hubs Data Sender**. This is the flow `simulator`
  has actually been tested against (both its connection-string and
  `AzureCliCredential` code paths work here).
- **Workspace identity available — local auth disabled.** That
  principal gets **Azure Event Hubs Data Receiver** instead, so
  Eventstream authenticates via Entra ID rather than a shared access key
  — see "Eventstream's Event Hub source auth" below.

### Prerequisites

Beyond `terraform.tfvars` values, the deploying identity and machine need
the following in place. Each of these tends to surface only partway through
`terraform apply` (earlier resources create fine before the failure), so
confirm them up front rather than discovering them one apply at a time:

- **A Fabric-licensed identity recognized by the Connections API.** The
  deploying user must be able to create Fabric Connections, not just browse
  the Fabric portal — general workspace access (a Power BI Pro/PPU license
  alone) is not sufficient in every tenant. If `terraform plan` fails on
  `fabric_connection.event_hub` with `UserNotLicensed`/401 despite portal
  access working, use a dedicated Fabric administrator account instead.
- **Fabric capacity admins all in one tenant.** Every UPN/object ID in
  `fabric_capacity_admin_members` must belong to the same AAD tenant as the
  deploying identity — capacity creation fails ("All administrators must
  belong to the same tenant") otherwise.
- **RBAC: Owner, or Contributor + User Access Administrator, on the target
  resource group.** This config creates several `azurerm_role_assignment`
  resources (Event Hub, Search, and Cognitive Services scopes); Contributor
  alone can't write role assignments and fails with `AuthorizationFailed`.
- **Azure OpenAI quota for the deployed model/SKU in the target region**,
  confirmed before applying (`az cognitiveservices usage list --location
  <region>`, checking the relevant `OpenAI.<Sku>.<model>` entry). This
  config deploys `gpt-5.4-mini` on the `DataZoneStandard` SKU (see
  `azure.tf`) rather than `GlobalStandard`, since `GlobalStandard` quota for
  this model can be 0 in a given subscription/region regardless of other
  deployments already running elsewhere in the same subscription.
- **Fabric IQ (Ontology) enabled at the tenant level.** The Ontology item
  this config creates requires a Fabric Admin Portal tenant setting for
  Fabric IQ / Ontology (preview) to be turned on — separate from general
  Fabric licensing above. `null_resource.deploy_ontology` fails with
  `FeatureNotAvailable` if it isn't (visible in the script's own error
  output as an HTTP 403 with `errorCode: FeatureNotAvailable`).
- **A CA bundle that trusts your org's TLS-inspecting proxy, if any** (e.g.
  Zscaler). Several `local-exec` provisioners (`load_kql`,
  `load_dimension_tables`, `load_kb_files`, `deploy_ontology`, and others)
  call Fabric/Kusto endpoints directly via Python's `requests`, which fails
  with `SSLCertVerificationError` if the proxy's root CA isn't in a bundle
  `requests`/`certifi` trusts. Export `SSL_CERT_FILE` and
  `REQUESTS_CA_BUNDLE` to a bundle containing that root CA before running
  `terraform apply` — on macOS, `security find-certificate -a -p
  /Library/Keychains/System.keychain` concatenated with certifi's own
  `cacert.pem` covers it.
- **[`uv`](https://docs.astral.sh/uv/)** on the machine running `terraform
  apply` — several `local-exec` provisioners shell out via `uv run --with
  ...`, e.g. `null_resource.load_kql`'s `uv run --with azure-kusto-data
  --with azure-identity ../fabric/eventhouse/run_kql.py`, which deploys
  `fabric/eventhouse/01`-`03`'s KQL and seeds the `ref_*` dimension tables
  from `fabric/ontology/tables/*.csv` (skipped if already populated, so
  re-applies don't duplicate rows).

### Deploy

For the full end-to-end procedure (this step plus running the
simulator and querying the agent), see
[`../INSTRUCTIONS.md`](../INSTRUCTIONS.md). Just this step:

```bash
cp terraform.tfvars.example terraform.tfvars   # fill in the Azure/Fabric values
terraform init
terraform apply
```

`subscription_id` is mandatory (pins the deployment explicitly rather
than relying on whatever's active in the deployer's `az` CLI context —
a real risk when working across multiple subscriptions/resource groups
in the same session). `resource_group_name` must already exist unless
`create_resource_group = true`, in which case Terraform creates it.

Nothing manual is left after `terraform apply` completes, assuming the
prerequisites above are met.

### Reproducing on a different subscription

Everything above is a single `terraform apply` against any subscription
with an existing resource group — nothing in `.tf` files is hardcoded
to this specific subscription, tenant, or resource names (checked live:
no subscription/tenant GUIDs anywhere in `.tf`; every ID comes from
`data.azurerm_client_config.current` or a variable). Two things are
still outside Terraform's reach, both genuine platform gaps rather than
missing config here:

- **Fabric IQ region availability.** Per Microsoft's own docs, Fabric
  IQ (the Ontology's underlying workload) "isn't available in regions
  where Power BI is the only Fabric workload" — confirm the target
  `var.location` supports the full Fabric stack before applying
  ([Fabric region availability](https://learn.microsoft.com/en-us/fabric/admin/region-availability#power-bi)).
  Not something Terraform can validate without an extra API call, so
  it isn't enforced here.
- **Whoever *queries* the agent needs their own real Fabric workspace
  access — this is not automated by `terraform apply` and is not the
  same person/identity as "whoever ran `terraform apply`" unless
  they're the same.** Two of the agent's three tools
  (`fabric_data_agent`, `fabric_iq_ontology`) use `authType:
  "UserEntraToken"` connections, which forward the *calling user's own
  signed-in identity* to Fabric — verified live to be the only
  combination that actually works (see `foundry/agents/README.md`'s
  "The Foundry agent" section for why `ProjectManagedIdentity` and a
  `MicrosoftFabric`-category connection both failed). Whoever runs
  `terraform apply` automatically becomes the Fabric workspace's Admin
  (Fabric's own behavior for the workspace creator, not something this
  config grants), so that person can query the agent successfully
  right away. Anyone else who needs to query it — a different demo
  presenter, a teammate testing independently — needs an explicit
  Fabric workspace role of their own
  (`fabric_workspace_role_assignment` in `fabric.tf` shows the
  pattern, e.g. the `search_contributor` block) before their queries
  to the two Fabric tools will succeed; without it, expect the same
  "technical error"/`403` failures documented for non-interactive
  identities, since an unauthorized real user hits the same wall a
  service principal does.

Confirmed live by actually redeploying into a second resource group
(`rg-fabcon-demo`, same subscription) — three more things Terraform
alone doesn't cover, found this way rather than assumed:

- **Fabric workspace and connection display names are unique
  tenant-wide, not per-workspace or per-resource-group.** The first
  redeploy attempt failed with `WorkspaceNameAlreadyExists` (the
  workspace's `new_workspace_display_name`) and then
  `DuplicateConnectionName` (`fabric_connection.event_hub`'s
  `display_name`) — both fixed by suffixing with `local.suffix`, the
  same deterministic per-RG suffix already used for ARM resource names
  elsewhere in this file. If you see either error, check that whatever
  supplied the colliding name is unique across the whole tenant, not
  just within this deployment.
- **The Fabric Data Agent must be published before it can be
  queried**, and nothing in this repo automated that until now
  (`null_resource.publish_data_agent`, `fabric/data-agent/publish_data_agent.py`)
  — it had only ever been run by hand against the original deployment,
  so a fresh redeploy 404'd ("while enumerating tools") until this was
  added.
- **OneLake shortcut creation can 400 for a short window right after
  `04_onelake_mirroring.kql` runs** — the KQL command accepting a
  table's mirroring policy doesn't mean OneLake's own view of that
  table is queryable yet. `fabric/ontology/deploy_onelake_shortcuts.py`
  now retries with backoff instead of failing on the first attempt.

### Operations Agent — one-time manual portal step required

`fabric_operations_agent.predictive_maintenance` (preview resource,
`provider "fabric" { preview = true }` in `providers.tf`) monitors
`silver_tempering.CrystalFormIndex` and is meant to alert when a batch
drifts outside the Form V target range. Two genuine platform bugs were
found live while wiring this up, confirmed by isolating each with raw
REST calls against the Fabric API directly (bypassing Terraform) rather
than assumed from the Terraform error alone:

- **An `OntologyDefinitions` entry in the `playbook` that isn't
  referenced by any `RuleDefinition` (via a `RuleCondition` or an
  `ActionBinding` parameter) makes item creation fail with a
  `500 InternalError`**, or hang until the provider's create timeout
  (raised to 30m here) is exceeded. Not documented anywhere found via
  Microsoft Learn/GitHub search. The fix is structural, not
  configurable: every property defined in `OntologyDefinitions` must
  be referenced somewhere in `RuleDefinitions`.
- **`dataSources[].id` and `shouldRun` are only reliably applied on the
  item's initial creation call.** Re-pushing the same, valid definition
  via `updateDefinition` — which is what a routine `terraform apply`
  does once the resource already exists in state — silently resets
  `dataSources[].id` to all-zeros and `shouldRun` to `false`, leaving
  the agent `Inactive` with a broken data-source binding. Confirmed by
  fetching the item's live definition via `getDefinition` after both a
  fresh `POST .../items` (correct) and an `updateDefinition` call with
  byte-identical content (broken) — not a one-off, reproduced twice.
  `fabric.tf`'s resource block sets `lifecycle { ignore_changes =
  [definition] }` specifically to stop any future `terraform apply`
  from ever re-pushing a definition update to this resource and
  re-triggering the bug.

Because of the second bug, **this resource cannot be made to reach a
working, running state through the API alone.** `fabric_activator.
operations_agent_connector` provisions the Activator item the alert
action's Power Automate connection is stored on, but the connection
itself and the flow it triggers are portal/Power-Automate-maker-only —
no REST or Terraform surface exists for either. After `terraform
apply` creates both items, finish the setup by hand once in the Fabric
portal following `doc/operations-agent-setup.md`. This is the one part
of this repo's Fabric estate that isn't fully reproducible via
`terraform apply` alone — everything else
documented in this file is.

### Capacity and trial-tenant limitation

The `microsoft/fabric` provider's own docs list a **known limitation**:
*"Microsoft Fabric trial capacity is not supported. Only self-provisioned
Fabric Capacity on Azure is supported."* That's why `fabric.tf`
provisions a real Azure-provisioned `Microsoft.Fabric/capacities`
resource via `azapi` (default SKU **F2**, configurable via
`fabric_capacity_sku`) rather than assuming one already exists or
falling back to a trial workspace — there's no supported Terraform path
for the latter. Independently, a trial capacity wouldn't work for this
demo anyway: Microsoft's own trial-capacity docs list "AI Experiences
such as Data agent" as unsupported on a trial, and `fabric_data_agent.business`
is one of this demo's three core Foundry agent tools.

### Nightly auto-pause

Since a paid capacity is a flat per-minute charge while `Active`
regardless of use, `fabric.tf` also provisions an Azure Automation
Account + PowerShell runbook (`fabric_capacity_auto_pause_enabled`,
default `true`) that suspends the capacity every night at
`fabric_capacity_auto_pause_time_utc` (default `20:00` UTC) — so
forgetting to pause it after a session doesn't mean paying for it
overnight or over a weekend. Resume is deliberately manual (demo/
rehearsal timing is too irregular for a fixed auto-resume schedule to
help): run `fabric/manage_capacity.py resume` before a session, and
either let the nightly schedule pause it again afterward or run
`fabric/manage_capacity.py pause` yourself when done for the day.

**Always run `fabric/manage_capacity.py resume` before `terraform
plan`/`apply` if the capacity might be paused.** Confirmed live: while
paused, the `fabric` provider can't read the workspace's items at all,
and `terraform plan` responds by showing the already-existing
Eventhouse/KQL database/Lakehouse/SQL database as "will be created" —
`data.fabric_capacity`'s own postcondition fails loudly before that
plan can be generated specifically to catch this, but the underlying
risk (destroying and recreating the whole Fabric data estate) is real
if that check is ever bypassed.

`fabric.tf` then creates a dedicated workspace on that capacity, and
`enable_workspace_identity = true` sets `identity = { type =
"SystemAssigned" }` on it — the workspace's `service_principal_id` then
feeds the Event Hub Data Receiver role assignment automatically
(`fabric.tf`'s `workspace_identity_service_principal_id` local, read by
`azure.tf`), rather than being a value you'd have to find and paste in by
hand.

### Agent tracing and observability

`azurerm_application_insights.foundry` (workspace-based, backed by
`azurerm_log_analytics_workspace.foundry`) is connected to the Foundry
project via `azapi_resource.foundry_tracing_connection` (`category =
"AppInsights"`) — creating that connection is what turns on Foundry's
automatic agent tracing, no separate enable step needed. It captures
per-tool-call spans (MCP calls, nested Data Agent/KQL calls, final
model synthesis) with real start/end timestamps, queryable via:

```bash
az monitor app-insights query --app <AZURE_APP_INSIGHTS_NAME> \
  --resource-group <resource-group> \
  --analytics-query "dependencies | where timestamp > ago(1h) | order by timestamp asc"
```

Confirmed live this is genuinely useful for diagnosing agent latency:
traced a slow multi-tool query and found tool calls execute strictly
sequentially with zero overlap (each starts the instant the previous
one ends) regardless of `parallel_tool_calls` or prompt instructions —
a Foundry Agent Service platform behavior for remote MCP tools, not
something fixable from the agent or prompt side.

### Why Terraform, not Bicep

Bicep can only reach `Microsoft.Fabric/capacities` (see below) — nothing
else on the Fabric side is an ARM resource, so a Fabric config needs
Terraform's `microsoft/fabric` provider regardless. `fabric.tf`
provisions that one ARM resource too, via the `azapi` provider, so the
capacity, the workspace/items, and the Event Hub all live in one
Terraform state and one `apply`. `sources/fabric-data-generation`
itself uses Bicep (its own `event-hub.bicep` was the reference for the
role-assignment pattern in `azure.tf`), so if this demo ever needs to
match that accelerator's deployment convention exactly, reverting that
one file to Bicep is a small, isolated change — everything else in this
repo stays independent of that choice either way.

## What can actually be IaC-scripted on the Fabric side

Researched against Microsoft's own docs/repos before writing anything —
summarized here since it shapes what a future `infra` addition
should use, rather than guessed at.

### Bicep/ARM: almost nothing

Only **`Microsoft.Fabric/capacities`** (the underlying Azure capacity SKU
purchase — F2–F2048) is a real ARM/Bicep resource — provisioned here via
`fabric.tf`, using the `azapi` provider rather than Bicep so it stays
in the same Terraform state as everything else. Workspaces and every
item inside one (Eventhouse, KQL Database, Eventstream, Lakehouse,
Warehouse, SQL Database, Connections...) are **not** ARM resources — they
live in Fabric's own management plane, not Azure Resource Manager's.

### Terraform provider for Microsoft Fabric — the real IaC path

[`microsoft/fabric`](https://registry.terraform.io/providers/microsoft/fabric/latest)
is now **generally available** and is the most complete option today.
Confirmed resources directly relevant to this repo:

- `fabric_workspace` (+ `data.fabric_workspace`, `data.fabric_capacity`)
  — now in `fabric.tf`
- `fabric_eventhouse`, `fabric_kql_database` — now in `fabric.tf`.
  `fabric_kql_database`'s `definition` attribute takes a
  `DatabaseSchema.kql`-shaped bundle (Fabric's Git-integration format for
  a KQL database's schema), **not** an arbitrary ordered script — an
  earlier version of this doc claimed `fabric/eventhouse/01`–`03` could be
  applied through it directly; that's still unconfirmed, since whether
  that format tolerates `.alter table policy streamingingestion` and
  `.create-or-alter materialized-view` was never worth the risk to test
  once the `local-exec` path below was already proven live, statement by
  statement. `fabric.tf` creates the KQL Database as a container, and a
  `null_resource` (see "Deploy" above) runs `01`–`03` against it
- `fabric_eventstream` — now in `fabric.tf`, takes
  `fabric/eventhouse/eventstream.json` as its definition verbatim, with its
  `<PLACEHOLDER>` tokens filled via `TextReplace` parameters rather than
  editing the file (verified: `processing_mode` must be exactly
  `"Parameters"`, capitalized, and `format = "Default"` is required
  alongside `definition` — both caught by `terraform validate`, not
  obvious from the docs alone)
- `fabric_connection` — now in `fabric.tf`, creating the Event Hub
  connection itself. `type = "EventHub"` / `creation_method =
  "EventHub.Contents"` and their `endpoint`/`entityPath` parameters
  aren't documented in the provider's reference docs; they were pulled
  from a live call to the Fabric `ListSupportedConnectionTypes` REST API
  against this tenant (see "Eventstream's Event Hub source auth" below)
  rather than guessed
- `fabric_lakehouse`, `fabric_warehouse`, plus domain/role-assignment and
  Spark-environment resources — relevant once Supply Chain/ERP lands in
  `fabric-ontology`'s Fabric SQL DB, not used here yet

Practical implication, and why the Event Hub above is Terraform too:
`Microsoft.Fabric/capacities` has an `azurerm`-adjacent path (`azapi`,
now `fabric.tf`) that works fine alongside `microsoft/fabric` in one
Terraform state — there's no need to split Azure and Fabric resources
across Bicep and Terraform when Terraform alone covers both.

### Fabric CLI (`fab`) and `fabric-cicd`

[`microsoft/fabric-cli`](https://github.com/microsoft/fabric-cli) is a
scriptable, filesystem-like CLI (`fab ls`, `fab get`, `fab deploy`) good
for CI/CD item deployment, not environment provisioning. `fabric-cicd`
(Python package) is described by Microsoft as the most widely-adopted
deployment tool for this same item-deployment layer. Both sit above
Terraform in the stack: Terraform (or manual setup) creates the
workspace and capacity attachment; `fab`/`fabric-cicd` push item
definitions into it. This is close to what
`sources/fabric-data-generation/infra/scripts/fabric` and
`sources/fabric-ontology/infra/scripts/post-provision` already hand-roll
via direct Fabric REST calls in Python — those scripts are a third,
lower-level option, and the one both vendored accelerators actually use
today.

### Eventstream's Event Hub source auth — Basic vs. Extended

Directly relevant to this config's two modes: adding an Azure Event Hub
as an Eventstream **source** supports Shared Access Key always, and
**workspace identity** (Entra ID) only under "Extended features" —
requiring Workspace Identity to be turned on for the workspace first
(Workspace settings → Workspace identity), then that identity's
principal ID granted **Azure Event Hubs Data Receiver** on the Event Hub
in Azure. That grant is what `workspace_identity_principal_id` here
automates; enabling Workspace Identity itself is a one-time, Fabric-side
manual step with no Bicep/Terraform path found during this research.
[Source](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/add-source-azure-event-hubs)

The connection itself (`fabric_connection.event_hub` in `fabric.tf`) is
created by Terraform now — the Fabric REST API's
[`ListSupportedConnectionTypes`](https://learn.microsoft.com/en-us/rest/api/fabric/core/connections/list-supported-connection-types)
endpoint, called live against this tenant, is the source of truth for a
connector's `type`/`creationMethod`/parameters, since none of that is in
the provider's or the REST API's own reference docs for this connector
specifically:

```json
{
  "type": "EventHub",
  "creationMethods": [
    {
      "name": "EventHub.Contents",
      "parameters": [
        { "name": "endpoint", "dataType": "Text", "required": true },
        { "name": "entityPath", "dataType": "Text", "required": true }
      ]
    }
  ],
  "supportedCredentialTypes": ["OAuth2", "Basic", "WorkspaceIdentity"]
}
```

Notably there's no SAS-specific credential type — the Fabric UI's
"Shared Access Key" auth kind (the only kind documented for the Basic
feature level) maps to `credentialType: "Basic"`, with `username` = the
SAS policy name and `password` = the SAS key. `fabric.tf` uses the
listen-only SAS rule from `azure.tf`
(`azurerm_eventhub_authorization_rule.eventstream_listen`) for that, or
`credentialType: "WorkspaceIdentity"` when `enable_workspace_identity =
true`.

### Running the KQL through Terraform

`fabric/eventhouse/01`–`03` are applied by `null_resource.load_kql`'s
`local-exec` provisioner (see "Deploy" above and
[`../fabric/eventhouse/run_kql.py`](../fabric/eventhouse/run_kql.py)) rather than
`fabric_kql_database`'s `definition` attribute — no Terraform-native
resource here covers arbitrary Kusto control commands, and the
`definition` attribute's exact tolerance for update policies and
materialized views is unconfirmed (see the `fabric_kql_database` note
above). `run_kql.py` reuses the same statement-splitting approach
verified live against this tenant, command by command, this session.

### Foundry IQ knowledge base

`azurerm_search_service.kb` (Basic tier), `fabric_workspace_role_assignment.search_contributor`,
`null_resource.load_kb_files`, and `null_resource.deploy_search_indexer`
provision and verify a complete, queryable Foundry IQ **knowledge
base** over `foundry/kb/*.md` — the data source, index, indexer,
knowledge source, and knowledge base itself, end to end. The knowledge
base is just another Azure AI Search data-plane object
(`PUT .../knowledgebases/{name}`), same family as
datasources/indexes/indexers/knowledgesources, so it's fully
automatable from `deploy_search_indexer.py` — no portal step needed to
create it. Confirmed live end to end (`4/4` docs indexed, test query
for "overdue invoice" correctly surfaces `03-erp-orders.md`, the
knowledge base answers questions correctly in the Foundry portal).
Requirements beyond the documented happy path:

- The OneLake files indexer's minimum required Fabric workspace role is
  **Contributor**, not Viewer (per
  [Microsoft Learn](https://learn.microsoft.com/en-us/azure/search/search-how-to-index-onelake-files)'s
  "Grant permissions" section).
- OneLake's `metadata_storage_path` values are full URLs
  (`https://onelake.blob.fabric.microsoft.com/...`), which contain `:`
  and `/` — invalid characters for a Search index key. The standard
  blob-indexer `fieldMappings` `base64Encode` function on the key field
  handles this (see `foundry/kb/deploy_search_indexer.py`).
- **The knowledge source needs a semantic configuration on its
  underlying index to be usable.** `searchIndex`-kind knowledge source
  objects (`PUT .../knowledgesources/{name}`) wrap the index; one
  lacking a `semantic.configurations[]` block isn't eligible.
- **Semantic ranking must also be enabled at the service level**, a
  separate control-plane setting from the index's own
  `semantic.configurations[]` block — `azurerm_search_service.kb`'s
  `semantic_search_sku = "free"` covers this (1,000 semantic
  queries/month at no extra cost).
- **The knowledge base object itself requires `api-version=2026-05-01-preview`**
  — the GA version used for every other call here doesn't yet support
  fields like `outputMode`/`retrievalReasoningEffort`.
- `retrievalReasoningEffort: {"kind": "minimal"}` is the one reasoning
  tier that doesn't require an attached model deployment; Low/Medium do.
- **The Search service must accept AAD tokens, not just API keys, for
  any RemoteTool/ProjectManagedIdentity connection to it to work.**
  `azurerm_search_service` defaults to `authOptions: apiKeyOnly`, which
  rejects every AAD/RBAC token outright regardless of role assignments
  -- a role assignment being correct and 40+ minutes old is not the
  same as the service accepting AAD auth at all. `azurerm_search_service.kb`'s
  `authentication_failure_mode = "http403"` argument is what flips this
  to `aadOrApiKey` (verified live via `az search service show
  --query authOptions`; the argument has no description in the
  provider's own schema, so this isn't obvious from `terraform plan`
  alone).

**Wiring the knowledge base into the Foundry project as an agent tool**
is a project-level connection, not a Search-service-object setting, so
it lives in `azure.tf` alongside the project rather than in
`foundry/kb/`. No `azurerm` resource models it:
`azurerm_cognitive_account_connection_*` is account-scoped, not
project-scoped, and its `category` argument is hard-validated to
`["AIServices", "AzureKeyVault", "AzureOpenAI", "AzureStorageAccount"]`
— `"RemoteTool"` is rejected by `terraform validate` itself, before any
API call. `azapi_resource.foundry_iq_kb_connection` calls the ARM
connections API directly instead
(`Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview`,
`PUT`/`DELETE` on
`.../accounts/{account}/projects/{project}/connections/{name}`), the
same endpoint
[Microsoft's own Foundry IQ docs](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/foundry-iq-connect)
use directly via `requests.put(...)` rather than an SDK method — there
is no `AIProjectClient` method for creating this connection either.
`schema_validation_enabled = false` is required on that resource: the
`azapi` provider's bundled schema for this preview API version
predates `authType: "ProjectManagedIdentity"` and rejects it
client-side even though the live API accepts it (confirmed against the
docs above, which require exactly that value for this scenario — the
alternative `ManagedIdentity` value `azapi` suggests instead is not the
same auth type). Auth to call the connection API itself is a normal
ARM/management-plane token (`https://management.azure.com/.default`),
distinct from the Foundry project's own data-plane token scope used
later to create/invoke an agent.
