# Setup

End-to-end steps to deploy this stack and run the demo: infra, the
simulator, and the live agent — plus the full technical detail for
each piece of the stack (deploy order, live-verified bugs/gotchas,
what was tried and abandoned). For *what* this demo is and *why* it's
built this way, see [`README.md`](README.md). For what the chocolate
factories themselves actually do, see
[`CHOCOLATE-FACTORY.md`](CHOCOLATE-FACTORY.md).

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Deploy the infrastructure](#2-deploy-the-infrastructure)
3. [Run the simulator](#3-run-the-simulator)
4. [The Fabric data estate](#4-the-fabric-data-estate)
5. [The Foundry agent spine](#5-the-foundry-agent-spine)
6. [Query the demo agent](#6-query-the-demo-agent)
7. [One-time manual step: Operations Agent](#7-one-time-manual-step-operations-agent)
8. [Pausing when done](#8-pausing-when-done)

## 1. Prerequisites

Beyond filling in `infra/terraform.tfvars`, the deploying identity and
machine need the following. Each of these tends to surface only
partway through `terraform apply` (earlier resources create fine
before the failure), so confirm them up front rather than discovering
them one apply at a time:

- **A Fabric-licensed identity recognized by the Connections API.**
  The deploying user must be able to create Fabric Connections, not
  just browse the Fabric portal — general workspace access (a Power
  BI Pro/PPU license alone) is not sufficient in every tenant. If
  `terraform plan` fails on `fabric_connection.event_hub` with
  `UserNotLicensed`/401 despite portal access working, use a
  dedicated Fabric administrator account instead.
- **Fabric capacity admins all in one tenant.** Every UPN/object ID in
  `fabric_capacity_admin_members` must belong to the same AAD tenant
  as the deploying identity — capacity creation fails ("All
  administrators must belong to the same tenant") otherwise.
- **RBAC: Owner, or Contributor + User Access Administrator, on the
  target resource group.** This config creates several
  `azurerm_role_assignment` resources (Event Hub, Search, and
  Cognitive Services scopes); Contributor alone can't write role
  assignments and fails with `AuthorizationFailed`.
- **Azure OpenAI quota for the deployed model/SKU in the target
  region**, confirmed before applying (`az cognitiveservices usage
  list --location <region>`, checking the relevant
  `OpenAI.<Sku>.<model>` entry). This config deploys `gpt-5.4-mini` on
  the `DataZoneStandard` SKU (see `infra/azure.tf`) rather than
  `GlobalStandard`, since `GlobalStandard` quota for this model can be
  0 in a given subscription/region regardless of other deployments
  already running elsewhere in the same subscription.
- **Fabric IQ (Ontology) enabled at the tenant level.** A Fabric Admin
  must enable **"Users can create Ontology (preview) items"** in the
  Fabric Admin Portal (Admin Portal → Tenant settings), separate from
  general Fabric licensing above — without it,
  `null_resource.deploy_ontology` fails outright with
  `FeatureNotAvailable` (an HTTP 403 with `errorCode:
  FeatureNotAvailable` in the script's own error output). This is the
  only tenant setting Microsoft's own docs list as required
  ([Ontology (Preview) Required Tenant Settings](https://learn.microsoft.com/en-us/fabric/iq/ontology/overview-tenant-settings));
  an earlier version of this doc speculated that two more
  specifically-named settings ("Users can create Graph", "Users can
  create and share data agent item types") were also required, based
  on an in-product warning banner — a side-by-side diff of two
  tenants' full settings lists found neither setting listed anywhere
  in either tenant, and the real cause turned out to be unrelated (see
  the `fabric/ontology` section's "A correctly-deployed definition
  still needs a static binding" below). Don't chase that banner's
  exact wording as a tenant setting; it doesn't correspond to anything
  in the admin portal's actual settings list.
- **A CA bundle that trusts your org's TLS-inspecting proxy, if any**
  (e.g. Zscaler). Several `local-exec` provisioners (`load_kql`,
  `load_dimension_tables`, `load_kb_files`, `deploy_ontology`, and
  others) call Fabric/Kusto endpoints directly via Python's
  `requests`, which fails with `SSLCertVerificationError` if the
  proxy's root CA isn't in a bundle `requests`/`certifi` trusts.
  Export `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` to a bundle
  containing that root CA before running `terraform apply` — on
  macOS, `security find-certificate -a -p
  /Library/Keychains/System.keychain` concatenated with certifi's own
  `cacert.pem` covers it.
- **[`uv`](https://docs.astral.sh/uv/)** on the machine running
  `terraform apply` — both the Terraform `local-exec` provisioners and
  the simulator shell out through it (e.g.
  `null_resource.load_kql`'s `uv run --with azure-kusto-data --with
  azure-identity ../fabric/eventhouse/run_kql.py`).

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
completes except the one-time Operations Agent portal step in §7.

If the Fabric capacity was paused (nightly auto-pause is on by
default), resume it before planning/applying — a paused capacity makes
`terraform plan` misread existing Fabric items as needing re-creation:

```bash
uv run fabric/manage_capacity.py resume
```

`subscription_id` is mandatory (pins the deployment explicitly rather
than relying on whatever's active in the deployer's `az` CLI context —
a real risk when working across multiple subscriptions/resource groups
in the same session). `resource_group_name` must already exist unless
`create_resource_group = true`, in which case Terraform creates it.

### What's in `infra/`

Azure-side IaC for the chocolate factory demo, plus what's
realistically IaC-able on the Fabric side today (research below —
nothing there is scripted yet).

One Terraform state (not Bicep — see "Why Terraform, not Bicep"
below), Azure and Fabric together:

- `providers.tf` — `azurerm`, `microsoft/fabric`, `azapi`, and `time`
  providers, all defaulting to Azure CLI auth (`az login`), no
  separate setup
- `azure.tf` — variables, resources, and outputs for the Azure Event
  Hub Namespace + Event Hub + role assignments (that `simulator`
  streams to and `fabric.tf`'s Connection reads from), the Azure AI
  Search service backing the Foundry IQ knowledge base, and a
  Microsoft Foundry project (`azurerm_cognitive_account` with
  `kind = "AIServices"` + `azurerm_cognitive_account_project` — the
  modern project-only shape, no Key Vault/Storage Account/Hub
  dependency like the older
  `azurerm_ai_foundry`/`azurerm_ai_foundry_project` pair, which
  provisions the legacy hub-based architecture that Foundry IQ
  knowledge sources explicitly don't support) for the agent spine
  (`foundry/agents/`) — including the 3 `azapi_resource` project
  connections (knowledge base, Fabric Data Agent, Fabric IQ Ontology)
  and the `null_resource` that deploys the actual Foundry Agent
  Service agent itself (`foundry/agents/deploy_foundry_agent.py`)
  wired to all 3 as tools
- `fabric.tf` — variables, resources, and outputs for the whole Fabric
  side: the capacity itself (`Microsoft.Fabric/capacities`, F2 by
  default, configurable via `fabric_capacity_sku`, provisioned via the
  `azapi` provider since neither `azurerm` nor `microsoft/fabric`
  expose that ARM resource), a dedicated workspace on it, Eventhouse,
  KQL Database, the Fabric Connection to the Event Hub, the
  Eventstream item itself (built from
  `fabric/eventhouse/eventstream.json`), a Lakehouse holding the
  ontology's dimension tables, a mirror of the Supply Chain/ERP tables
  (for Ontology binding — see §4), *and* the `foundry/kb/*.md`
  knowledge-base docs, a Fabric SQL Database holding the authoritative
  Supply Chain/ERP tables, a Fabric IQ Ontology (preview) bound across
  the Eventhouse and Lakehouse, a Fabric Data Agent grounded in the
  Eventhouse — Factory/Quality only; a second Lakehouse-backed source
  for Supply Chain/ERP was tried and abandoned, see §4's
  `fabric/data-agent` section — a workspace role assignment granting
  the Azure AI Search service (`azure.tf`) Contributor so its OneLake
  indexer can read the Lakehouse, and `null_resource`s
  (`hashicorp/null` provider) that deploy the Bronze/Silver/Gold KQL,
  the dimension Lakehouse tables, the Supply Chain/ERP SQL schema +
  seed data, the Ontology definition, the KB markdown uploads, and the
  Search data-source/index/indexer, all via `local-exec` provisioners
- `terraform.tfvars.example` — copy to `terraform.tfvars` and fill in
  the Azure/Fabric values

Deployed and verified end to end against a real tenant: `terraform
apply` provisions the F2 capacity, workspace, Eventhouse, KQL
database, Connection, Eventstream, Bronze/Silver/Gold KQL + reference
data, dimension Lakehouse, Fabric SQL Database + Supply Chain/ERP
data, Fabric IQ Ontology, the Foundry IQ knowledge base, the Foundry
project and model deployment, all 3 agent tool connections, and the
Foundry Agent Service agent itself, wired to all 3 tools — and
`simulator` streams events all the way through to the Gold layer. One
`terraform apply` against an existing resource group in any
subscription/tenant reproduces the whole stack; see "Reproducing on a
different subscription" below for the couple of things genuinely
outside Terraform's reach.

Event Hub auth, chosen by whether a workspace identity is available
(`workspace_identity_principal_id`, resolved automatically from
`fabric.tf` when `enable_workspace_identity = true` — see below):

- **Default — SAS, local auth enabled.** The deploying user gets
  **Azure Event Hubs Data Sender**. This is the flow `simulator` has
  actually been tested against (both its connection-string and
  `AzureCliCredential` code paths work here).
- **Workspace identity available — local auth disabled.** That
  principal gets **Azure Event Hubs Data Receiver** instead, so
  Eventstream authenticates via Entra ID rather than a shared access
  key — see "Eventstream's Event Hub source auth" below.

`fabric.tf` then creates a dedicated workspace on that capacity, and
`enable_workspace_identity = true` sets `identity = { type =
"SystemAssigned" }` on it — the workspace's `service_principal_id`
then feeds the Event Hub Data Receiver role assignment automatically
(`fabric.tf`'s `workspace_identity_service_principal_id` local, read
by `azure.tf`), rather than being a value you'd have to find and paste
in by hand.

#### Fabric workspace items, explained

`terraform apply` doesn't account for everything that shows up in the
Fabric workspace item list — some items are Fabric's own side effects
of creating another item, not something this repo's Terraform
provisions directly. Full inventory, confirmed live via the Fabric
REST API's `list-items` call against a deployed workspace:

| Item | Managed by | Notes |
|---|---|---|
| Eventhouse `chocolate-factory-eventhouse` | `fabric_eventhouse.this` | The real Eventhouse container |
| KQLDatabase `chocolate-factory-eventhouse` | Fabric (auto-created) | Empty, unused default companion database Fabric always creates alongside a new Eventhouse, sharing its exact display name — not in Terraform state, `terraform destroy` doesn't touch it, safe to ignore |
| KQLDatabase `chocolate-factory-kql` | `fabric_kql_database.this` | The real database — Bronze/Silver/Gold medallion tables live here |
| Lakehouse `chocolate_factory_dimensions` | `fabric_lakehouse.dimensions` | Static dimension tables (no timestamp, can't live in the Eventhouse) |
| SQLDatabase `chocolate_factory_business` | `fabric_sql_database.business` | Supply Chain/ERP tables |
| Ontology `ChocolateFactory` | `null_resource.deploy_ontology` (script-driven — no native Terraform resource due to an `ALMOperationImportFailed` limitation) | Binds the Eventhouse, dimensions Lakehouse, and SQL Database |
| Lakehouse/GraphModel `ChocolateFactory_lh_<id>` / `ChocolateFactory_graph_<id>` | Fabric (auto-created by the Ontology) | Internal storage backing the Ontology's queryable graph — follows from the Ontology itself being unmanaged by Terraform |
| SQLEndpoint (one per Lakehouse/SQL Database) | Fabric (auto-created) | Read-only T-SQL analytics endpoint Fabric provisions automatically for every Lakehouse/SQL Database |
| Eventstream `chocolate-factory-eventstream` | `fabric_eventstream.this` | Event Hub → Bronze ingestion |
| DataAgent `chocolate_factory_data_agent` | `fabric_data_agent.business` | Grounds in Eventhouse Silver tables only |
| OperationsAgent `chocolate_factory_predictive_maintenance` | `fabric_operations_agent.predictive_maintenance` | Watches `silver_tempering.CrystalFormIndex` |
| Reflex `chocolate-factory-operations-agent-connector` | `fabric_activator.operations_agent_connector` | Stores the Operations Agent's alert Power Automate connection |

**The default companion database is harmless — leave it.** Fabric
auto-creates it with every Eventhouse; it's empty, costs nothing
extra, and Terraform can neither create nor destroy it deliberately
(`fabric_kql_database`'s `database_type` only supports `ReadWrite` or
`Shortcut` — no way to adopt/import an already-existing default,
confirmed against the provider's schema). Delete it manually in the
portal only if the item list bothers you; there's no functional effect
either way.

**Names mix hyphens and underscores on purpose, not by accident.**
Lakehouse- and Ontology-family item types reject hyphens in Fabric's
API ("DisplayName is Invalid for ArtifactType"), so those items use
underscores while everything else uses hyphens.

### Reproducing on a different subscription

Everything above is a single `terraform apply` against any
subscription with an existing resource group — nothing in `.tf` files
is hardcoded to this specific subscription, tenant, or resource names
(checked live: no subscription/tenant GUIDs anywhere in `.tf`; every
ID comes from `data.azurerm_client_config.current` or a variable). Two
things are still outside Terraform's reach, both genuine platform gaps
rather than missing config here:

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
  combination that actually works (see §5's `foundry/agents` section
  for why `ProjectManagedIdentity` and a `MicrosoftFabric`-category
  connection both failed). Whoever runs `terraform apply`
  automatically becomes the Fabric workspace's Admin (Fabric's own
  behavior for the workspace creator, not something this config
  grants), so that person can query the agent successfully right
  away. Anyone else who needs to query it — a different demo
  presenter, a teammate testing independently — needs an explicit
  Fabric workspace role of their own (`fabric_workspace_role_assignment`
  in `fabric.tf` shows the pattern, e.g. the `search_contributor`
  block) before their queries to the two Fabric tools will succeed;
  without it, expect the same "technical error"/`403` failures
  documented for non-interactive identities, since an unauthorized
  real user hits the same wall a service principal does.

Confirmed live by actually redeploying into a second resource group
(`rg-fabcon-demo`, same subscription) — three more things Terraform
alone doesn't cover, found this way rather than assumed:

- **Fabric workspace and connection display names are unique
  tenant-wide, not per-workspace or per-resource-group.** The first
  redeploy attempt failed with `WorkspaceNameAlreadyExists` (the
  workspace's `new_workspace_display_name`) and then
  `DuplicateConnectionName` (`fabric_connection.event_hub`'s
  `display_name`) — both fixed by suffixing with `local.suffix`, the
  same deterministic per-RG suffix already used for ARM resource
  names elsewhere in this file. If you see either error, check that
  whatever supplied the colliding name is unique across the whole
  tenant, not just within this deployment.
- **The Fabric Data Agent must be published before it can be
  queried**, and nothing in this repo automated that until now
  (`null_resource.publish_data_agent`,
  `fabric/data-agent/publish_data_agent.py`) — it had only ever been
  run by hand against the original deployment, so a fresh redeploy
  404'd ("while enumerating tools") until this was added.
- **OneLake shortcut creation can 400 for a short window right after
  `04_onelake_mirroring.kql` runs** — the KQL command accepting a
  table's mirroring policy doesn't mean OneLake's own view of that
  table is queryable yet. `fabric/ontology/deploy_onelake_shortcuts.py`
  now retries with backoff instead of failing on the first attempt.

### Capacity and trial-tenant limitation

The `microsoft/fabric` provider's own docs list a **known
limitation**: *"Microsoft Fabric trial capacity is not supported. Only
self-provisioned Fabric Capacity on Azure is supported."* That's why
`fabric.tf` provisions a real Azure-provisioned
`Microsoft.Fabric/capacities` resource via `azapi` (default SKU
**F2**, configurable via `fabric_capacity_sku`) rather than assuming
one already exists or falling back to a trial workspace — there's no
supported Terraform path for the latter. Independently, a trial
capacity wouldn't work for this demo anyway: Microsoft's own
trial-capacity docs list "AI Experiences such as Data agent" as
unsupported on a trial, and `fabric_data_agent.business` is one of
this demo's three core Foundry agent tools.

**Always run `fabric/manage_capacity.py resume` before `terraform
plan`/`apply` if the capacity might be paused.** Confirmed live: while
paused, the `fabric` provider can't read the workspace's items at
all, and `terraform plan` responds by showing the already-existing
Eventhouse/KQL database/Lakehouse/SQL database as "will be created" —
`data.fabric_capacity`'s own postcondition fails loudly before that
plan can be generated specifically to catch this, but the underlying
risk (destroying and recreating the whole Fabric data estate) is real
if that check is ever bypassed.

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
`fabric/manage_capacity.py pause` yourself when done for the day (see
§8).

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

Bicep can only reach `Microsoft.Fabric/capacities` (see below) —
nothing else on the Fabric side is an ARM resource, so a Fabric
config needs Terraform's `microsoft/fabric` provider regardless.
`fabric.tf` provisions that one ARM resource too, via the `azapi`
provider, so the capacity, the workspace/items, and the Event Hub all
live in one Terraform state and one `apply`. `sources/fabric-data-generation`
itself uses Bicep (its own `event-hub.bicep` was the reference for the
role-assignment pattern in `azure.tf`), so if this demo ever needs to
match that accelerator's deployment convention exactly, reverting
that one file to Bicep is a small, isolated change — everything else
in this repo stays independent of that choice either way.

### What can actually be IaC-scripted on the Fabric side

Researched against Microsoft's own docs/repos before writing
anything — summarized here since it shapes what a future `infra`
addition should use, rather than guessed at.

#### Bicep/ARM: almost nothing

Only **`Microsoft.Fabric/capacities`** (the underlying Azure capacity
SKU purchase — F2–F2048) is a real ARM/Bicep resource — provisioned
here via `fabric.tf`, using the `azapi` provider rather than Bicep so
it stays in the same Terraform state as everything else. Workspaces
and every item inside one (Eventhouse, KQL Database, Eventstream,
Lakehouse, Warehouse, SQL Database, Connections...) are **not** ARM
resources — they live in Fabric's own management plane, not Azure
Resource Manager's.

#### Terraform provider for Microsoft Fabric — the real IaC path

[`microsoft/fabric`](https://registry.terraform.io/providers/microsoft/fabric/latest)
is now **generally available** and is the most complete option today.
Confirmed resources directly relevant to this repo:

- `fabric_workspace` (+ `data.fabric_workspace`, `data.fabric_capacity`)
  — now in `fabric.tf`
- `fabric_eventhouse`, `fabric_kql_database` — now in `fabric.tf`.
  `fabric_kql_database`'s `definition` attribute takes a
  `DatabaseSchema.kql`-shaped bundle (Fabric's Git-integration format
  for a KQL database's schema), **not** an arbitrary ordered script —
  whether that format tolerates `.alter table policy
  streamingingestion` and `.create-or-alter materialized-view` is
  unconfirmed, since it wasn't worth the risk to test once the
  `local-exec` path below was already proven live, statement by
  statement. `fabric.tf` creates the KQL Database as a container, and
  a `null_resource` (see "Running the KQL through Terraform" below)
  runs `01`–`03` against it
- `fabric_eventstream` — now in `fabric.tf`, takes
  `fabric/eventhouse/eventstream.json` as its definition verbatim,
  with its `<PLACEHOLDER>` tokens filled via `TextReplace` parameters
  rather than editing the file (verified: `processing_mode` must be
  exactly `"Parameters"`, capitalized, and `format = "Default"` is
  required alongside `definition` — both caught by `terraform
  validate`, not obvious from the docs alone)
- `fabric_connection` — now in `fabric.tf`, creating the Event Hub
  connection itself. `type = "EventHub"` / `creation_method =
  "EventHub.Contents"` and their `endpoint`/`entityPath` parameters
  aren't documented in the provider's reference docs; they were
  pulled from a live call to the Fabric `ListSupportedConnectionTypes`
  REST API against this tenant (see "Eventstream's Event Hub source
  auth" below) rather than guessed
- `fabric_lakehouse`, `fabric_warehouse`, plus domain/role-assignment
  and Spark-environment resources — relevant once Supply Chain/ERP
  lands in a Fabric SQL DB elsewhere, not used further here

Practical implication, and why the Event Hub above is Terraform too:
`Microsoft.Fabric/capacities` has an `azurerm`-adjacent path (`azapi`,
now `fabric.tf`) that works fine alongside `microsoft/fabric` in one
Terraform state — there's no need to split Azure and Fabric resources
across Bicep and Terraform when Terraform alone covers both.

#### Fabric CLI (`fab`) and `fabric-cicd`

[`microsoft/fabric-cli`](https://github.com/microsoft/fabric-cli) is a
scriptable, filesystem-like CLI (`fab ls`, `fab get`, `fab deploy`)
good for CI/CD item deployment, not environment provisioning.
`fabric-cicd` (Python package) is described by Microsoft as the most
widely-adopted deployment tool for this same item-deployment layer.
Both sit above Terraform in the stack: Terraform (or manual setup)
creates the workspace and capacity attachment; `fab`/`fabric-cicd`
push item definitions into it. This is close to what direct Fabric
REST calls in Python (as used throughout `fabric/` and `foundry/` in
this repo) already do — a third, lower-level option.

#### Eventstream's Event Hub source auth — Basic vs. Extended

Directly relevant to this config's two modes: adding an Azure Event
Hub as an Eventstream **source** supports Shared Access Key always,
and **workspace identity** (Entra ID) only under "Extended features"
— requiring Workspace Identity to be turned on for the workspace
first (Workspace settings → Workspace identity), then that identity's
principal ID granted **Azure Event Hubs Data Receiver** on the Event
Hub in Azure. That grant is what `workspace_identity_principal_id`
here automates; enabling Workspace Identity itself is a one-time,
Fabric-side manual step with no Bicep/Terraform path found during
this research.
([Source](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/add-source-azure-event-hubs))

The connection itself (`fabric_connection.event_hub` in `fabric.tf`)
is created by Terraform now — the Fabric REST API's
[`ListSupportedConnectionTypes`](https://learn.microsoft.com/en-us/rest/api/fabric/core/connections/list-supported-connection-types)
endpoint, called live against this tenant, is the source of truth for
a connector's `type`/`creationMethod`/parameters, since none of that
is in the provider's or the REST API's own reference docs for this
connector specifically:

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
feature level) maps to `credentialType: "Basic"`, with `username` =
the SAS policy name and `password` = the SAS key. `fabric.tf` uses
the listen-only SAS rule from `azure.tf`
(`azurerm_eventhub_authorization_rule.eventstream_listen`) for that,
or `credentialType: "WorkspaceIdentity"` when
`enable_workspace_identity = true`.

#### Running the KQL through Terraform

`fabric/eventhouse/01`–`03` are applied by
`null_resource.load_kql`'s `local-exec` provisioner (see
`fabric/eventhouse/run_kql.py`) rather than `fabric_kql_database`'s
`definition` attribute — no Terraform-native resource here covers
arbitrary Kusto control commands, and the `definition` attribute's
exact tolerance for update policies and materialized views is
unconfirmed (see the `fabric_kql_database` note above). `run_kql.py`
reuses the same statement-splitting approach verified live against
this tenant, command by command, this session.

#### Foundry IQ knowledge base

`azurerm_search_service.kb` (Basic tier),
`fabric_workspace_role_assignment.search_contributor`,
`null_resource.load_kb_files`, and
`null_resource.deploy_search_indexer` provision and verify a
complete, queryable Foundry IQ **knowledge base** over
`foundry/kb/*.md` — the data source, index, indexer, knowledge
source, and knowledge base itself, end to end. The knowledge base is
just another Azure AI Search data-plane object
(`PUT .../knowledgebases/{name}`), same family as
datasources/indexes/indexers/knowledgesources, so it's fully
automatable from `deploy_search_indexer.py` — no portal step needed
to create it. Confirmed live end to end (`4/4` docs indexed, test
query for "overdue invoice" correctly surfaces `03-erp-orders.md`,
the knowledge base answers questions correctly in the Foundry
portal). Requirements beyond the documented happy path:

- The OneLake files indexer's minimum required Fabric workspace role
  is **Contributor**, not Viewer (per
  [Microsoft Learn](https://learn.microsoft.com/en-us/azure/search/search-how-to-index-onelake-files)'s
  "Grant permissions" section).
- OneLake's `metadata_storage_path` values are full URLs
  (`https://onelake.blob.fabric.microsoft.com/...`), which contain
  `:` and `/` — invalid characters for a Search index key. The
  standard blob-indexer `fieldMappings` `base64Encode` function on
  the key field handles this (see
  `foundry/kb/deploy_search_indexer.py`).
- **The knowledge source needs a semantic configuration on its
  underlying index to be usable.** `searchIndex`-kind knowledge
  source objects (`PUT .../knowledgesources/{name}`) wrap the index;
  one lacking a `semantic.configurations[]` block isn't eligible.
- **Semantic ranking must also be enabled at the service level**, a
  separate control-plane setting from the index's own
  `semantic.configurations[]` block — `azurerm_search_service.kb`'s
  `semantic_search_sku = "free"` covers this (1,000 semantic
  queries/month at no extra cost).
- **The knowledge base object itself requires
  `api-version=2026-05-01-preview`** — the GA version used for every
  other call here doesn't yet support fields like
  `outputMode`/`retrievalReasoningEffort`.
- `retrievalReasoningEffort: {"kind": "minimal"}` is the one reasoning
  tier that doesn't require an attached model deployment; Low/Medium
  do.
- **The Search service must accept AAD tokens, not just API keys,
  for any RemoteTool/ProjectManagedIdentity connection to it to
  work.** `azurerm_search_service` defaults to `authOptions:
  apiKeyOnly`, which rejects every AAD/RBAC token outright regardless
  of role assignments — a role assignment being correct and 40+
  minutes old is not the same as the service accepting AAD auth at
  all. `azurerm_search_service.kb`'s `authentication_failure_mode =
  "http403"` argument is what flips this to `aadOrApiKey` (verified
  live via `az search service show --query authOptions`; the argument
  has no description in the provider's own schema, so this isn't
  obvious from `terraform plan` alone).

**Wiring the knowledge base into the Foundry project as an agent
tool** is a project-level connection, not a Search-service-object
setting, so it lives in `azure.tf` alongside the project rather than
in `foundry/kb/`. No `azurerm` resource models it:
`azurerm_cognitive_account_connection_*` is account-scoped, not
project-scoped, and its `category` argument is hard-validated to
`["AIServices", "AzureKeyVault", "AzureOpenAI", "AzureStorageAccount"]`
— `"RemoteTool"` is rejected by `terraform validate` itself, before
any API call. `azapi_resource.foundry_iq_kb_connection` calls the ARM
connections API directly instead
(`Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview`,
`PUT`/`DELETE` on
`.../accounts/{account}/projects/{project}/connections/{name}`), the
same endpoint
[Microsoft's own Foundry IQ docs](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/foundry-iq-connect)
use directly via `requests.put(...)` rather than an SDK method —
there is no `AIProjectClient` method for creating this connection
either. `schema_validation_enabled = false` is required on that
resource: the `azapi` provider's bundled schema for this preview API
version predates `authType: "ProjectManagedIdentity"` and rejects it
client-side even though the live API accepts it (confirmed against
the docs above, which require exactly that value for this scenario —
the alternative `ManagedIdentity` value `azapi` suggests instead is
not the same auth type). Auth to call the connection API itself is a
normal ARM/management-plane token
(`https://management.azure.com/.default`), distinct from the Foundry
project's own data-plane token scope used later to create/invoke an
agent.

## 3. Run the simulator

### One-time setup

```bash
cd simulator
uv sync
cp .env.sample .env   # fill in your Event Hub connection string or namespace
```

`AZURE_EVENT_HUB_NAMESPACE_HOSTNAME` and `AZURE_EVENT_HUB_NAME` come
from `infra`'s `AZURE_EVENT_HUB_NAMESPACE_HOSTNAME`/`AZURE_EVENT_HUB_NAME`
outputs (`terraform output`) if you're using `az login` auth instead
of a connection string — the deploying identity already has **Azure
Event Hubs Data Sender** on that Event Hub from the deploy.

Dimension CSVs (`fabric/ontology/tables/*.csv` — factories, lines,
recipes) are committed in the repo and already loaded into Fabric by
`terraform apply`; only re-run `uv run run_seed_data.py` if you want
to regenerate them.

### Real-time streaming — baseline demo run

```bash
uv run --env-file .env run_simulator.py --interval 5 --anomaly-rate 0.03 --downtime-rate 0.015
```

Streams live telemetry for all 4 factories. Verified live: ~700-750
events over 2 minutes with no connection errors. Add `--max-runtime
120` to auto-stop, or Ctrl+C. Start this ~2 minutes before going
live — let the room see numbers moving before any slide changes.

### Day-before prep — backfilling history

A fresh environment has no multi-day history, which the Gold layer's
daily-bucketed views (`gold_defect_rate_by_stage_daily`,
`gold_factory_oee_daily`) need. Run this the day before a session (or
any time you want fresh trend data):

```bash
cd simulator
./prepare_demo_data.sh          # 30 days ending now, the default
./prepare_demo_data.sh 14       # or any other window, in days
```

One command does the full sequence: resumes the Fabric capacity if
it's paused, **clears any previously streamed telemetry first**
(`fabric/eventhouse/clear_telemetry.py`) so re-running this never
double-counts an earlier backfill, streams the requested window
(`--interval 300 --backfill-batch-ticks 100` under the hood — fast: a
30-day window takes roughly 3-4 minutes), then waits for ingestion to
settle and verifies the result (`fabric/eventhouse/verify_telemetry.py`
— row counts, daily density, a duplicate-`ReadingId` check). Requires
`az login` as an identity with Fabric + Event Hub access, and reads
connection details straight from `terraform output` in `infra/`, so
`terraform apply` must have already succeeded.

**Why clearing first matters:** `bronze_sensor_reading`,
`bronze_quality_check`, and `bronze_line_status` use queued ingestion
(streaming ingestion is disabled on them for Silver's update-policy
joins), which can take a few minutes to land — a naive re-run of
`--backfill-hours` without clearing first will layer a second,
independently-sampled dataset on top of the first for any overlapping
time window, silently doubling event density and skewing daily
aggregations. `prepare_demo_data.sh` avoids this by construction;
running the lower-level `run_simulator.py --backfill-hours` command
directly (below) does not.

Lower-level, manual equivalent if you want more control over a single
step:

```bash
uv run --env-file .env run_simulator.py --backfill-hours 48 --interval 30 --backfill-batch-ticks 100
```

Virtual clock, no wall-clock sleep — 48h at 30s/tick is 5760 ticks,
sent in batches of 20 (`--backfill-batch-ticks`) to avoid Event Hub
throttling. A lighter `--interval` keeps the total tick/event count
reasonable for a larger backfill (48h at the default 5s interval is
34560 ticks; at 30s it's 5760) — stage progression (`TicksPerBatch`,
anomaly/downtime rates) is unaffected either way, since those are
per-tick, not per-second. Mutually exclusive with `--max-runtime`.

### Scenario variants

The simulator has no per-scenario flag — anomaly/downtime are per-tick
probabilities applied uniformly to every line. Dial the rate up for a
short bounded run right before you need a specific demo beat:

| Scenario | Command | What it shows |
|---|---|---|
| **Normal** | `--anomaly-rate 0.03 --downtime-rate 0.015` | Baseline — realistic occasional blips, most quality checks pass |
| **Quality anomaly burst** | `--anomaly-rate 0.5 --max-runtime 60` | Sensor values pushed outside their normal range, driving `DefectRate` up and `quality_check.Result = "Fail"` — good for *"are there any anomalies I should be aware of?"* |
| **Line downtime** | `--downtime-rate 0.3 --max-runtime 60` | Lines flip to `Down` with a reason (Scheduled Maintenance / Unplanned Stop / Changeover), stop emitting readings |
| **Tempering / Operations Agent trigger** | `--anomaly-rate 1.0`, let it run a full batch cycle | Guarantees a `CrystalFormIndex` reading outside its normal range during the Tempering stage — what `fabric_operations_agent.predictive_maintenance` watches for its Form V drift alert. Requires the §7 manual portal step to actually fire. |

At `--anomaly-rate 1.0`, a line takes a full cycle (26 ticks across
all 6 stages) to reach Tempering — let it run at least that long
rather than a very short `--max-runtime`.

### What the simulator does

Streams simulated production-line telemetry for the four chocolate
factories (see [`CHOCOLATE-FACTORY.md`](CHOCOLATE-FACTORY.md)) to
Azure Event Hub, in the exact shape the `sensor_reading`,
`quality_check`, `batch`, and `line_status` tables define in
`fabric/ontology/ontology_config.json` — so the ontology and the
generator can never drift apart on field names.

Each production line (2-3 per factory, see `src/seed_data.py`) cycles
continuously through the six in-factory stages defined in
`src/stage_catalog.py`: `grinding -> mixing_refining -> conching ->
tempering -> molding_cooling -> packaging`. Every tick, a line emits
one of four record types (see `src/simulator.py`):

- **`sensor_reading`** — one row per metric for the line's current
  stage (EAV shape: `Metric`, `Value`, `Unit` — adding a metric later
  needs no schema change)
- **`quality_check`** — on the stage's last tick, with a `DefectRate`
  derived from how far that tick's readings sat outside normal range.
  Carries `LineId` directly (not just `BatchId`) so Silver can enrich
  it without joining through the async `silver_batch` materialized
  view.
- **`batch_event`** — `Started` at the first tick of grinding,
  `Completed` at the last tick of packaging. This is what
  `silver_batch` should read instead of inferring timing from
  `sensor_reading`.
- **`line_status`** — `Down` when a line enters a randomized
  maintenance window (`--downtime-rate`, default 1.5%/tick), `Running`
  when it exits. While down, a line emits nothing else — no readings,
  no quality checks. Feeds Availability in `gold_factory_oee_daily`.

Verified against a real Event Hub: an 18-second bounded run
(`--max-runtime 18`) streamed 219 events across all four record types
with no connection errors.

Notes:

- `production_line`/`factory`/`production_stage`/`recipe` are static
  reference CSVs, not streamed — `sensor_reading`, `quality_check`,
  `batch_event`, and `line_status` are the four live record types.
- Supply Chain and ERP/Orders entities (supplier, material, inventory,
  shipment, customer, product, sales_order, order_line, invoice) are
  batch-generated by `run_business_seed.py` (`src/business_data.py`),
  not streamed — see §4's `fabric/sql-database` section.
- Bronze/Silver/Gold KQL and the Eventstream definition live in
  `fabric/eventhouse/` — see §4.

### Linting

```bash
cd simulator
uv run ruff check .     # lint
uv run ruff format .    # format
```

## 4. The Fabric data estate

### `fabric/eventhouse`

Bronze/Silver/Gold KQL for the chocolate factory Eventhouse.

#### Two data planes

Medallion tiering earns its keep where data arrives fast and raw —
that's Factory/Quality telemetry. Supply Chain and ERP/Orders are
low-volume and already typed at the source — they get a lighter
Bronze→Silver→Gold parallel inside the Fabric SQL Database instead of
a second Eventhouse pipeline (see `fabric/sql-database` below).

| Plane | Domain | Mechanism |
|---|---|---|
| Eventhouse / KQL DB | Factory/Quality — streaming | High-frequency `sensor_reading` + `quality_check` from `simulator`. Update policies + materialized views do the tiering |
| Fabric SQL Database | Supply Chain & ERP/Orders — batch | Low-volume dimensional/transactional tables, already typed in `ontology_config.json`. Tiering is CSV load → typed table → SQL view |

```
data-generation --Event Hub-->  Bronze  --update policy: pivot + join dims-->  Silver
                                                                                   |
                                                          materialized view: rollups
                                                                                   v
                                                                                 Gold
                                                                                   |
                                                                  Factory/Quality agent
                                                                    (queries Gold only)
```

#### Deploy order

`infra`'s `terraform apply` runs all of this automatically, in order,
via `null_resource.load_kql`'s `local-exec` provisioner
(`fabric/eventhouse/run_kql.py` — see §2's "Running the KQL through
Terraform"). The order, for anyone iterating on the KQL directly
against a queryset instead:

1. **`01_bronze_and_reference.kql`** — 4 Bronze tables (one per
   `RecordType` the generator streams, matching what
   `simulator/src/simulator.py` emits with no transform), 4 reference
   tables (native ingested copies of the seeded CSVs — never a
   OneLake shortcut, since shortcuts can't back an MV or a joining
   update policy — see "Reality check" below), and the
   `streamingingestion` disables that the Silver joins below need.
2. **`02_silver.kql`** — Bronze is EAV by design (a new metric needs
   no schema change), but that's awkward to query. Silver pivots
   `bronze_sensor_reading` into one wide table per stage — each stage
   has a different metric set, so a single union table would be
   mostly nulls: 6 stage-pivot tables (update policy, pivots the EAV
   readings + joins in dimensions via a shared `EnrichLine()`
   function), enriched `silver_quality_check` and `silver_line_status`,
   and `silver_batch` (the one true materialized view — reads the
   explicit lifecycle events in `bronze_batch_event` directly, no
   inferring timing from readings, single source, no join).
3. **`03_gold.kql`** — Pre-aggregated, semantically named, cheap to
   query — the Factory/Quality specialist should never touch Bronze
   or Silver directly. `gold_defect_rate_by_stage_daily` (a real
   materialized view — single source, `silver_quality_check`) plus
   three functions (`gold_line_throughput_hourly()`,
   `gold_batch_summary()`, `gold_factory_oee_daily()`) that join
   across Silver tables at query time, since Kusto materialized views
   can't have more than one source and can't sit on top of another
   materialized view. Both materialized views use `.create-or-alter`,
   not `.create`, so `run_kql.py`/a re-`apply` can redeploy them
   without an `EntityAlreadyExistsException` (`.create
   materialized-view` isn't idempotent — hit this live before
   switching).
4. **`eventstream.json`** — the Eventstream item definition: one
   `AzureEventHub` source, a `Filter` operator per `RecordType`
   (`sensor_reading`/`quality_check`/`batch_event`/`line_status`), and
   one `Eventhouse` destination per Bronze table. Column lists in each
   destination's `inputSchema` match the Bronze table schemas and the
   generator's payload fields exactly. `infra`'s `fabric.tf` creates
   this item automatically (Fabric Connection + Eventstream), filling
   in its placeholders itself.
5. **`run_kql.py`** also loads `fabric/ontology/tables/*.csv` (written
   by `simulator/run_seed_data.py`) into the four `ref_*` tables —
   skipped per-table once it already has rows, so re-running is safe.
   Without this, Silver enrichment columns (`LineName`, `FactoryCode`,
   ...) stay blank. Doing this by hand instead: `.ingest inline into
   table ref_factory <| ...` per table works for local/dev use; a
   real deployment should use a Fabric pipeline Copy activity instead.
6. **`04_onelake_mirroring.kql`** enables OneLake availability
   (mirroring to Delta Parquet) on `silver_quality_check`,
   `silver_line_status`, and the 6 per-stage tables — so
   `fabric/ontology/deploy_onelake_shortcuts.py` can expose them as
   ordinary Lakehouse tables for the Fabric IQ Ontology's relationship
   instances (Eventhouse tables can never be a Contextualization
   source directly). `silver_batch` is excluded: it's a materialized
   view, and the mirroring policy command only accepts `table`, not
   `materialized-view` (`.alter-merge materialized-view silver_batch
   policy mirroring ...` fails to parse at all; `.alter-merge table
   silver_batch policy mirroring ...` returns "the requested endpoint
   ... does not exist"). See `fabric/ontology`'s "Deploying to Fabric
   IQ" section below for which relationships this leaves type-only.

The `Filter` operator shape in `eventstream.json` is taken directly
from Microsoft's own
[fabric-event-streams](https://github.com/microsoft/fabric-event-streams)
template repo (`API Templates/eventstream-definition.json`), not
reverse-engineered — this is the one part of the whole pipeline
checked against a real reference example rather than docs prose
alone.

#### Verified against a live tenant

`01`–`03` have been run end to end against a real Fabric Eventhouse,
with the simulator actually streaming through Event Hub → Eventstream
→ Bronze → Silver → Gold. Four issues only surfaced this way, all
fixed in the KQL:

- **Bronze `Timestamp` is `string`, not `datetime`.** Eventstream's
  `ProcessedIngestion` auto-creates the Bronze tables from
  `eventstream.json`'s `inputSchema`, which types `Timestamp` as
  `Nvarchar(max)` (JSON has no native datetime) — and since `.create
  table` is a no-op against an already-existing table regardless of
  schema mismatches, `01`'s original `Timestamp: datetime` declaration
  silently never took effect. `01` now declares `Timestamp: string`
  to match reality, and every Silver transform casts with
  `todatetime(...)`.
- **`evaluate pivot()` can silently drop columns.** Update policies
  process one small ingestion batch/extent at a time; if a batch
  happens to carry zero rows for a given stage, `pivot()` produces no
  columns for that stage's metrics at all, and a plain `project
  Timestamp, ..., TemperatureC, ...` then fails to resolve the
  column — which, since these are `IsTransactional` policies, rolls
  back the *entire* ingestion batch and silently blocks Bronze itself
  from growing (visible via `.show ingestion failures`, not from the
  `.alter table policy update` command succeeding). Fixed by wrapping
  every pivoted metric column in `column_ifexists('X', real(null))`
  in the final `project` of each of the 6 stage transforms.
- **Materialized views only allow a table reference + one trailing
  `summarize`.** `silver_batch`'s original `| extend Status = ...`
  after the `summarize` was rejected outright; folding it into the
  `summarize` as an aggregation didn't work either (`iff` isn't a
  supported aggregation function for materialized views). Fixed by
  dropping `Status` from `silver_batch` entirely and deriving it
  downstream in `gold_batch_summary()` instead (a function, not an
  MV, so no such restriction).
- **`coalesce` needs matching branch types.**
  `gold_factory_oee_daily()`'s `coalesce(TotalDownMinutes, 0.0)` mixed
  `long` (from `datetime_diff`/`sum`) with `real` (`0.0`) — fixed
  with an explicit `toreal(TotalDownMinutes)`.

Two things this pass didn't verify: `gold_factory_oee_daily()`'s
`prev()`/`serialize` Down/Running pairing logic on a realistic
multi-day window (only tested against a few minutes of live data),
and Gold functions' behavior once `join` columns actually collide
across Silver tables at scale.

**OneLake mirroring latency**: `TargetLatencyInMinutes=5` is a
target, not a guarantee — Microsoft's own docs cite up to 3 hours in
the worst case. Enabling the policy and creating the shortcut both
succeed immediately and the table's schema appears in OneLake right
away, but the first real data commit can take noticeably longer than
5 minutes in practice; query the shortcut's row count (or check for
`.parquet` files under `Tables/<name>/` via the OneLake DFS API)
rather than assuming data is present immediately after enabling.

#### Reality check: what Kusto's own docs actually allow

Checked against Microsoft Learn before writing any KQL.

- **Shortcuts can't back an MV or a joining update policy —
  confirmed.** "Materialized views can't be defined over external
  tables." / "[An update policy] can't access external data or
  external tables." Bites the four reference tables that every Silver
  update policy joins against. Fixed by loading them as native
  ingested tables from the seeded CSVs, never a OneLake shortcut.
  ([Materialized views limitations](https://learn.microsoft.com/en-us/kusto/management/materialized-views/materialized-views-limitations?view=microsoft-fabric),
  [Update policy overview](https://learn.microsoft.com/en-us/kusto/management/update-policy?view=microsoft-fabric))
- **An MV has exactly one source table — and can't sit on another MV
  — confirmed, plus an extra constraint.** "A materialized view can't
  be created on top of another materialized view, unless the first
  materialized view is of type `take_any(*)` aggregation." Bites
  `gold_line_throughput_hourly` and `gold_batch_summary` (two Silver
  sources each), and `gold_factory_oee_daily` worst of all — it was
  drafted reading two other Gold materialized views directly, which
  is disallowed independent of the multi-source problem. Fixed: all
  three became stored **functions** that join live at query time
  instead of physicalized MVs. Fine at demo data volume; revisit
  physicalization only if query latency actually becomes a problem —
  it won't.
- **Joining update policies need streaming ingestion off, upstream
  too — confirmed, on by default in Fabric.** "By default, the
  Streaming ingestion policy is enabled for all tables in the
  Eventhouse. To use functions with the join operator in an update
  policy, the streaming ingestion policy must be disabled." — cascades
  to every upstream table in the chain. Bites `bronze_sensor_reading`,
  `bronze_quality_check`, and `bronze_line_status`. Fixed with
  `.alter table <name> policy streamingingestion disable` on all
  three — costs nothing here, since Eventstream's default write
  cadence into Eventhouse is already batched, not the special
  low-latency streaming mode.
- **OneLake availability mirrors Eventhouse → Delta, read-only —
  confirmed, one-directional.** "You can create a logical copy of KQL
  database data in an eventhouse by turning on OneLake
  availability... query the data in your KQL database in Delta Lake
  format through other Fabric engines." Doesn't bite anything in the
  current design — agent-level joins were chosen over mirroring for
  the cross-plane decision (see the Decisions list below). Per-table
  or per-database toggle, adaptive write latency 5 min–3 hr, read-only
  Delta copy, reachable via a Lakehouse/Warehouse shortcut or the
  database's SQL endpoint — it only moves data *out of* Eventhouse,
  the SQL DB side would need its own export path to go the other way.
  ([Turn on OneLake availability for an eventhouse](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-house-onelake-availability))

#### Decisions

1. **Cross-plane queries → agent-level join, no mirroring.** The
   Coordinator/agent calls the Factory/Quality specialist (Eventhouse)
   and the Supply Chain/ERP specialists (SQL DB) separately and
   synthesizes the answer itself. No OneLake shortcut, no unified
   query surface for this — revisit only if the demo script needs a
   genuinely single-query cross-domain answer.
2. **`silver_batch` freshness → explicit lifecycle events.** The
   generator emits a `bronze_batch_event` stream (BatchId, LineId,
   RecipeId, EventType: Started/Completed, Timestamp), at the first
   tick of grinding and the last tick of packaging. `silver_batch` is
   a clean materialized view over that — no scanning readings to infer
   status.
3. **Retention → one uniform policy, no per-tier tuning.** Same
   retention (e.g. 30 days) across Bronze/Silver/Gold. Differentiated
   TTL is a production storage-cost concern that doesn't apply at
   demo data volumes.
4. **OEE uptime → synthetic downtime signal in the generator.** Lines
   occasionally enter a "Down" maintenance window, streamed via
   `bronze_line_status`. Chosen over dropping Availability entirely
   because it gives the demo a real narrative beat — a line goes
   down, the Factory/Quality agent surfaces it, OEE dips and recovers
   on camera.

### `fabric/ontology`

Chocolate factory scenario in `ontology_config.json` + `tables/*.csv`
shape.

- `ontology_config.json` — tables, columns, types, keys, relationships
  for all three agent domains (Factory/Quality, Supply Chain,
  ERP/Orders).
- `tables/` — dimension CSVs. `factory.csv`, `production_line.csv`,
  `production_stage.csv`, and `recipe.csv` are generated by
  `simulator/run_seed_data.py` — run that first. `sensor_reading`,
  `quality_check`, `batch`, and `line_status` are streamed at runtime
  by the data generator, not seeded here (`batch` is materialized in
  Silver from the generator's `batch_event` stream — see
  `fabric/eventhouse` above).

#### Checking it visually

`generate_rdf.py` converts `ontology_config.json` into `chocolate.rdf`
(RDF/XML-OWL), the format
[microsoft/Ontology-Playground](https://github.com/microsoft/Ontology-Playground)
accepts on its hosted site
([microsoft.github.io/Ontology-Playground](https://microsoft.github.io/Ontology-Playground/))
with no setup. The Playground's JSON import is a legacy,
disabled-by-default feature with a different shape
(`entityTypes`/`cardinality`) than our `tables`/`relationships`, so
RDF is the actual interoperable path — and per the Playground's own
docs, it's also the exact format Microsoft Fabric IQ expects.

```bash
python generate_rdf.py          # regenerate chocolate.rdf after editing the config
```

Then on the Playground: Import/Export → Import → pick `chocolate.rdf`.
All 17 classes and 19 relationships should appear on the canvas,
grouped loosely by domain via each class's icon/color (🏭
Factory/Quality, 🚚 Supply Chain, 🧾 ERP/Orders).

#### Deploying to Fabric IQ

Deployed as a real Fabric IQ Ontology item (preview), bound directly
to the live Eventhouse and a small dimension Lakehouse:

- `generate_fabric_iq_definition.py` generates the Ontology's
  `EntityTypes`/`RelationshipTypes` definition into `fabric_iq/`.
- `deploy_dimension_lakehouse.py` loads `tables/*.csv` into a
  Lakehouse as real Delta tables (via the OneLake DFS API + the
  Lakehouse "Load Table" API) — Factory/Quality's dimension entities
  (`factory`, `production_line`, `production_stage`, `recipe`) bind
  here rather than the Eventhouse, since Eventhouse bindings are
  TimeSeries-only, and all 9 Supply Chain/ERP tables (`supplier`,
  `material`, `inventory`, `shipment`, `customer`, `product`,
  `sales_order`, `order_line`, `invoice`) are mirrored into the same
  Lakehouse for the same reason — the Ontology definition schema has
  no `SqlDatabaseTable`/`WarehouseTable` sourceType, only
  `LakehouseTable` and `KustoTable` (Eventhouse, TimeSeries-only). The
  Fabric SQL Database (`fabric/sql-database/`) stays the actual
  system of record; this Lakehouse copy exists solely to satisfy the
  Ontology's binding format.
- `deploy_onelake_shortcuts.py` creates OneLake shortcuts in the
  dimension Lakehouse pointing at Eventhouse tables with OneLake
  availability enabled (`fabric/eventhouse/04_onelake_mirroring.kql`)
  — `silver_quality_check`, `silver_line_status`, and the 6
  per-stage sensor tables. A shortcut is transparent to consumers, so
  these bind exactly like native Lakehouse tables, letting
  relationships whose "from" table is Eventhouse-bound get real
  Contextualization instances despite Eventhouse tables never being a
  valid Contextualization *source* directly.
- `materialize_static_sources.py` copies `batch`, `quality_check`, and
  `line_status` out of the Eventhouse into the dimension Lakehouse as
  real, native (non-shortcut) Delta tables — the required static
  binding source for those entities' TimeSeries data (see "A
  correctly-shaped definition is not enough" below). Re-run on every
  `terraform apply`, not once, unlike everything else here.
- `deploy_fabric_iq_ontology.py` deploys the generated definition via
  direct Fabric REST calls (not the `fabric_ontology` Terraform
  resource — verified live that Terraform-issued calls for it
  reliably fail against this tenant while identical direct REST calls
  succeed).

All four are wired into `infra/fabric.tf` as `null_resource`s and run
automatically on `terraform apply`.

**All 22 entities are bound**: every Factory/Quality entity (`batch`,
`quality_check`, `line_status`, the 4 dimension tables, and
`sensor_reading` realized as 6 per-stage entities — see
`generate_fabric_iq_definition.py`'s docstring for why a single
EAV-shaped entity isn't possible and how the per-stage split works)
plus all 9 Supply Chain/ERP entities. 27 of the 29 relationship types
have real Contextualization instances, including the cross-domain
`shipment_to_batch` link — only `batch_to_line` and `batch_to_recipe`
stay type-only, since `silver_batch` is a materialized view and
doesn't support the OneLake mirroring policy command (see
`fabric/eventhouse`'s "Verified against a live tenant" section
above).

**A correctly-shaped definition is not enough — every TimeSeries
entity also needs a static binding, which this generator didn't emit
for a while.** Confirmed live on a fresh deploy: the Ontology item,
all 22 entity types, all 27 Contextualizations, and every source
table's data were entirely correct, and `fabric_iq_ontology` queries
still failed with `search_ontology: The Graph Model is not ready` —
the GraphModel's own job history showed exactly one refresh attempt,
failed non-retriably with `GraphNotRefreshable: "Graph doesn't have
valid content and cannot be refreshed."`

Root cause, confirmed against Microsoft's own docs
([Bind Data - Microsoft Fabric](https://learn.microsoft.com/en-us/fabric/iq/ontology/how-to-bind-data)):
*"Before you bind time series data to an entity type, make sure your
static data binding is complete. The entity type must have at least
one property with static data bound to it."* `batch`, `quality_check`,
`line_status`, and all 6 `sensor_reading_<stage>` entities — 9 of the
22 — had their key column correctly carved out as a static
`properties` entry (required separately: "Entity keys can only
reference static properties"), but **no data binding was ever wired
to that static property**, only the TimeSeries one. The Fabric
portal's own entity type details page surfaces this directly:
`QualityCheck` showed **"Missing static binding."** The Prerequisites
tenant-setting theory in an earlier version of this doc was a red
herring — a side-by-side tenant settings diff found no relevant
setting difference at all; this was a definition-generation bug the
whole time.

**Fixed** by giving each of those 9 entities a second, `NonTimeSeries`
`DataBinding` (`STATIC_BINDING_SOURCE` in
`generate_fabric_iq_definition.py`). The static source must itself be
OneLake-backed and a **managed** table, not the OneLake-mirrored
*shortcuts* used for Contextualizations above (Fabric IQ's docs
explicitly exclude "external tables that show in the lakehouse but
reside in a different location") — so:
- The 6 `sensor_reading_<stage>` entities are keyed by `LineId` alone,
  which already exists in the native `production_line` table — their
  fix needed no new data, just a binding pointing there.
- `batch`/`quality_check`/`line_status` are keyed by `BatchId`/
  `CheckId`/`EventId`, which exist nowhere as a native table.
  `materialize_static_sources.py` (new) copies each one's full row out
  of the Eventhouse into a real Delta table in the dimension Lakehouse
  — unlike every other `null_resource` in `infra/fabric.tf`, this one
  is wired to run on **every** `terraform apply`
  (`triggers.always_run`), not once, since these three keep growing as
  the simulator streams.

There is still no public Fabric REST API to trigger a graph rebuild;
after redeploying the definition, it has to be retriggered from the
Ontology item's own page in the Fabric portal.

**Confirmed fixed live**: after `terraform apply` ran
`materialize_static_sources.py` (uploaded 3,010/18,024/2,548 rows for
`batch`/`quality_check`/`line_status` respectively, confirmed via
OneLake as real Delta parquet files) and redeployed the Ontology
definition (confirmed via `getDefinition` — `QualityCheck` now shows
both a `TimeSeries -> silver_quality_check` and a
`NonTimeSeries -> quality_check` binding), a manual graph refresh from
the Ontology item's page in the portal **succeeded**
(`GraphModel`'s own job history: `Completed`, no `failureReason`).

Worth noting for anyone hitting this fresh: the GraphModel's job
history actually shows *three* refresh attempts, not two — the
original hard failure right after a fresh deploy
(`GraphNotRefreshable`, non-retriable), then a `Completed` refresh the
next morning with no code changes in between, then today's `Completed`
refresh after this fix. The most likely explanation for the middle
one: OneLake mirroring latency (documented above, up to hours in the
worst case per Microsoft's own docs) — the shortcut-mirrored source
data for the Contextualizations probably hadn't propagated into
OneLake yet at the moment of the first attempt, and had by the next
morning, letting the job *complete* even though 9 entities still had
no static binding and came up empty individually (matching "Factory
has data, QualityCheck doesn't" being visible at that point). So: a
`Completed` graph refresh is necessary but not sufficient evidence
that a specific entity actually has data — check that entity's own
type details too.

**Separately, still an open question, not yet re-tested against the
now-working graph:** 16 of the 27 relationship Contextualizations
bind through the OneLake-mirror *shortcuts* (`quality_check`,
`line_status`, all 6 `sensor_reading_<stage>`), the same "external
table" pattern the docs say isn't supported for bindings in general.
Since the graph refresh above succeeded with those shortcuts still in
place, this concern may turn out to be moot for Contextualizations
specifically (unlike for entity-level static bindings, where it
mattered) — worth confirming next by checking whether the
relationships that depend on those shortcuts (e.g. `check_to_line`,
`line_status_to_line`) actually resolve real instance data in the
portal's graph view, not just that the refresh job itself succeeded.
If it turns out they don't, the next step is pointing those
Contextualizations at `quality_check`/`line_status`'s new native
tables from `materialize_static_sources.py` instead of the shortcuts
(the 12 `reading_<stage>_to_line`/`reading_<stage>_to_batch`
relationships would still need the shortcuts, or a similar
materialization, since their source tables have no native copy).

Also worth knowing if extending this further: `generate_fabric_iq_definition.py`'s
docstring documents a real correctness bug found and fixed in the
Contextualization key-binding logic — `sourceKeyRefBindings`/
`targetKeyRefBindings` name columns identifying *that side's own
entity*, not simply the relationship's declared `fromKey`/`toKey`
used symmetrically (which happened to produce valid-but-wrong
bindings for every relationship except `shipment_to_factory`, whose
foreign-key column name doesn't match its `toKey`).

Farm Preparation (harvest/fermentation, drying/roasting, winnowing) is
intentionally not simulated — it happens off-site, near cocoa origin.
See [`CHOCOLATE-FACTORY.md`](CHOCOLATE-FACTORY.md).

### `fabric/sql-database`

Supply Chain + ERP/Orders tables for the chocolate factory Fabric SQL
Database — the batch/transactional plane, as opposed to
`fabric/eventhouse`'s streaming Factory/Quality telemetry (see the
"Two data planes" design above). 9 tables across the two domains,
plus 3 Gold views.

- `01_tables.sql` — the 9 tables (`supplier`, `material`, `inventory`,
  `shipment`, `customer`, `product`, `sales_order`, `order_line`,
  `invoice`), matching `fabric/ontology/ontology_config.json`
  column-for-column.
- `02_gold.sql` — `gold_inventory_position` (stockout-risk flag per
  factory/material), `gold_supplier_scorecard` (rating + volume per
  supplier), `gold_order_fulfillment_kpi` (payment status per order —
  `NotInvoiced`/`Outstanding`/`Overdue`/`Paid`).
- `deploy_sql_database.py` — deploys both files and seeds the 9
  tables from `fabric/ontology/tables/*.csv` (written by
  `simulator/run_business_seed.py`). Uses
  [python-tds](https://python-tds.readthedocs.io/) (pure-Python TDS
  client) rather than `pyodbc`, so no native ODBC driver needs
  installing on the machine running `terraform apply` — same
  reasoning as every other script in this repo staying pure-Python.
  Auth is an Azure AD access token (`AzureCliCredential`, scope
  `https://database.windows.net/.default`).

`infra/fabric.tf` provisions the empty `fabric_sql_database` item and
runs this script automatically via `null_resource.load_business_sql`.

#### Verified against a live tenant

- `fabric_sql_database`'s computed `server_fqdn` comes back as
  `"<host>,<port>"` (e.g. `...database.fabric.microsoft.com,1433`),
  not a bare hostname — `deploy_sql_database.py` splits on the comma
  before handing it to `python-tds`.
- `python-tds` needs `cafile` set to actually enable TLS (there's no
  separate `encrypt=True` flag) — Fabric SQL Database requires it.
  `pyOpenSSL` also needs to be installed for `python-tds` to
  establish a TLS channel at all ("pyOpenSSL does not work"
  otherwise).
- `python-tds`'s own hostname-validation code (`tls.py`'s
  `validate_host`) hits an `AttributeError` against the installed
  pyOpenSSL/cryptography version — an internal library bug, not
  fixable from here. `deploy_sql_database.py` passes
  `validate_host=False` to skip it; safe here since the hostname
  comes straight from Terraform state, not untrusted input, and the
  connection is still TLS-encrypted via `cafile`.
- If the `local-exec` step fails with an SSL/cert error, the same
  corporate-TLS-proxy note in §1's Prerequisites applies — export
  `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE`.

#### Regenerating the seed data

```bash
cd simulator
uv run run_business_seed.py
```

Deterministic (fixed random seed + reference date), so re-running
produces byte-identical CSVs — see `src/business_data.py`'s docstring
for the business-rule vocabulary it finalizes (shipment/order
statuses, customer segments) that the KB docs left open.

### `fabric/data-agent`

Definition parts for `fabric_data_agent.business` (`infra/fabric.tf`),
the Fabric item the Foundry agent's `fabric_data_agent` tool calls
(see §5's `foundry/agents` section).

- `data_agent.json.tmpl`, `draft/stage_config.json.tmpl` (the AI
  instructions), and one `datasource.json.tmpl` per grounded source
  under `draft/kusto-eventhouse/`. Schema verified against
  [Microsoft Learn's Data Agent item definition article](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/data-agent-definition).
- `generate_data_agent_definition.py` generates the `elements` tree
  (which tables/columns are selected) — the API doesn't
  auto-discover a source's schema from just an artifactId; every
  table/column has to be listed explicitly with `is_selected: true`.
- `publish_data_agent.py` publishes the Data Agent
  (`infra/fabric.tf`'s `null_resource.publish_data_agent`) — a
  draft-only agent can't be queried at all.

#### Status

**Eventhouse-only, done and verified live.** Bound to the
Eventhouse's `silver_quality_check` and `silver_line_status` tables
via `type: "kusto"`. Confirmed answering real questions correctly
through the Data Agent's own MCP endpoint and the Fabric portal's own
chat panel (see "Testing it yourself" below) — e.g. "How many quality
checks failed today?" → correct, grounded answer.

`silver_batch` is excluded (materialized view, not a plain table —
showed a permission/deleted warning in the portal even with an
identical `is_selected: true` config to the two working tables; same
category of limitation that already blocked OneLake mirroring for it
elsewhere in this repo).

**Supply Chain/ERP via a second (Lakehouse) source: abandoned, not a
bug on our end.** The plan was one shared data agent spanning both
engines rather than one per domain — Fabric SQL Database directly
(`type: "data_warehouse"`) was ruled out first (doesn't function
against a genuine `SQLDatabase`-type item; no `sql_database` value
exists in the `type` enum at all). Switched to the dimension
Lakehouse's mirror of the same 9 tables instead, and tried five
configurations live:

1. Flat top-level table list (the shape that works for Kusto)
2. Wrapped in a `lakehouse_tables` root element
3. Top-level `type: "lakehouse"` instead of `"lakehouse_tables"`
   (rejected outright — "Data source type is immutable")
4. SQL-analytics-endpoint type names (`varchar`/`float`/`int`)
   instead of Delta type names
5. An **exact byte-for-byte reproduction** of a config built by
   removing and re-adding the source through the Fabric portal's own
   "+ Add data source" picker — confirmed in the portal's own Sources
   view as connected, no warning, all 21 tables visible under
   `Schemas > dbo > Tables`

Every one of the five failed identically: the agent reports "the
available data sources do not contain supplier information" — via
this agent's MCP endpoint **and** the portal's own native chat panel,
even for configuration (5), which the portal itself had just
generated and displayed as healthy. That last result is what rules
out a configuration mistake on our end — if the portal's own
generated config fails in the portal's own chat, the gap is in Data
Agent + Lakehouse Tables query execution for this data shape, not in
anything authored here. Supply Chain/ERP grounding for the Foundry
agent comes from the **Fabric IQ Ontology** instead (already covers
all 9 tables with 27 real relationships, verified working
independently of this issue).

#### Testing it yourself

The Data Agent has no plain REST "ask a question" endpoint —
querying goes over MCP. Minimal Python client (needs `az login`
first):

```python
import asyncio
import httpx
from azure.identity import AzureCliCredential
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

WORKSPACE_ID = "<FABRIC_WORKSPACE_ID>"
DATA_AGENT_ID = "<FABRIC_DATA_AGENT_ID>"
MCP_URL = f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{WORKSPACE_ID}/dataagents/{DATA_AGENT_ID}/agent"

async def main():
    credential = AzureCliCredential()
    token = credential.get_token("https://api.fabric.microsoft.com/.default").token
    http_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=120.0)
    async with streamable_http_client(MCP_URL, http_client=http_client) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tool = (await session.list_tools()).tools[0]
            result = await session.call_tool(tool.name, {"userQuestion": "How many quality checks failed today?"})
            for c in result.content:
                print(getattr(c, "text", c))

asyncio.run(main())
```

Requires the Data Agent to be **published** first (a draft-only agent
can't be queried at all):

```bash
curl -X POST "https://api.fabric.microsoft.com/v1/workspaces/<WORKSPACE_ID>/dataAgents/<DATA_AGENT_ID>/staging/publish" \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"description": "..."}'
```

`uv run --with azure-identity --with mcp --with httpx <script>.py` —
the `mcp` package's API has changed across versions
(`streamablehttp_client` → `streamable_http_client`, `headers=` kwarg
removed in favor of an `httpx.AsyncClient`, `tool.inputSchema` →
`tool.input_schema`); the snippet above is the current shape as of
this writing.

## 5. The Foundry agent spine

### `foundry/agents`

The "agent spine" on top of the already-built data spine (Fabric IQ
Ontology, Fabric SQL Database, Eventhouse, Foundry IQ knowledge base).

- `deploy_foundry_agent.py` creates/updates the actual Foundry Agent
  Service agent (`chocolate-factory-agent`) that ties the whole agent
  spine together — see "The Foundry agent" below.

The Fabric-side agent items this agent's tools are grounded in (Data
Agent, Operations Agent) live under `fabric/data-agent/` (§4) and
`fabric/operations-agent/` instead — they're Fabric items, not
Foundry ones.

#### The Foundry agent

**Done, verified live across all three domains.**
`chocolate-factory-agent` (in the `chocolate-factory` Foundry
project) is wired to 3 tools, all generic `type: "mcp"` referencing a
project connection (`infra/azure.tf`) rather than a bare `server_url`
+ static bearer header:

- **`knowledge_base`** — connection `chocolate-factory-kb`, category
  `RemoteTool`, authType `ProjectManagedIdentity`. Answers
  policy/definition questions (e.g. "What counts as an overdue
  invoice?") with citations.
- **`fabric_data_agent`** — connection `fabric-data-agent`, category
  `RemoteTool`, authType `UserEntraToken`. Answers Factory/Quality
  telemetry aggregates (e.g. "How many quality checks failed today?").
- **`fabric_iq_ontology`** — connection `fabric-iq-ontology`, category
  `RemoteTool`, authType `UserEntraToken`. Answers structured
  relationship questions across all 22 entity types (e.g. "What
  entity types exist in the ontology?").

The two Fabric tools needed `UserEntraToken` (forwards the calling
user's own signed-in identity) specifically because both Fabric
surfaces reject non-interactive identities for actual query
execution — confirmed live, not assumed:

- **`ProjectManagedIdentity` (same pattern as the knowledge base)
  doesn't work for either Fabric tool.** The connection itself
  authenticates fine, but the Fabric Data Agent returns an internal
  "technical error" from its own answer synthesis for any
  non-interactive identity — reproduced with both the project's own
  managed identity and an unrelated app-only service-principal
  token, ruling out a config-specific cause. The Ontology MCP
  endpoint has no application-only auth path at all, per
  [Microsoft's docs](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/fabric-iq):
  delegated auth is the only option.
- **The Foundry portal's own "Microsoft Fabric" connection wizard**
  (Settings → Connections → New connection → **Microsoft Fabric**,
  under Agent Knowledge Tools) is currently broken: its "Custom Keys"
  form (`workspace-id` + `artifact-id` fields) fails live with a bare
  `400`. Reproduced directly against the connections API: `category:
  "MicrosoftFabric"` only accepts `authType: "AAD"` or
  `"UserEntraToken"` — not `"CustomKeys"` as the form's own label
  implies — and `workspace-id`/`artifact-id` turn out to be plain
  connection `metadata`, not `credentials.keys` (`AAD` authType takes
  no credentials block at all). Building that corrected shape
  (`category: "MicrosoftFabric"`, `authType: "AAD"`) creates cleanly
  and validates, but every agent query against it failed with
  `"Connection resolution failed"` — this category+authType
  combination doesn't actually resolve to a usable identity at
  runtime, at least not as of this writing.
- **What actually works**: `category: "RemoteTool"` + `authType:
  "UserEntraToken"` + `audience:
  "https://analysis.windows.net/powerbi/api"` — the same pattern
  Microsoft's docs use for their one concrete non-Ontology example (a
  Data Agent behind a workspace private link), applied here without
  the private-link angle. Confirmed live end to end: both tools
  return real, correct answers, matching what a direct user-token MCP
  client gets (see §4's `fabric/data-agent` "Testing it yourself" for
  that client).

Querying the agent (once deployed):

```bash
TOKEN=$(az account get-access-token --scope https://ai.azure.com/.default --query accessToken -o tsv)
curl -s "https://aif-cacao-07499f0c.services.ai.azure.com/api/projects/chocolate-factory/openai/v1/responses" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"agent_reference": {"type": "agent_reference", "name": "chocolate-factory-agent"}, "input": [{"role": "user", "content": "How many quality checks failed today?"}]}'
```

Note the auth token scope for calling the agent
(`https://ai.azure.com/.default`) is different from the one Fabric
MCP clients use directly (`https://api.fabric.microsoft.com/.default`)
— the agent's own `UserEntraToken` connections handle the Fabric-side
token exchange internally, using whichever identity called the agent
in the first place.

### `foundry/kb`

Unstructured grounding documents for the chocolate factory agents —
process explanations, glossaries, and business rules that don't
belong in structured tables but that an agent needs to answer well.
Structured data lives in `fabric/ontology` (schema) and
`fabric/eventhouse` (Bronze/Silver/Gold); this folder is the
knowledge-base/RAG side, for Foundry IQ.

| File | Grounds |
|---|---|
| `00-company-overview.md` | Coordinator + all specialists — factories, the 9-stage process, recipes, domain boundaries |
| `01-factory-quality.md` | Factory/Quality specialist — sensor metric ranges and what they mean, quality-check/OEE definitions |
| `02-supply-chain.md` | Supply Chain specialist — materials, supplier rating, inventory/reorder logic |
| `03-erp-orders.md` | ERP/Orders specialist — products, customer segments, order/invoice lifecycle |

Note the version markers inside `02-supply-chain.md` and
`03-erp-orders.md`: those two domains have an ontology schema
(`fabric/ontology/ontology_config.json`) but no data generator yet,
so their KB docs define the intended business semantics ahead of
that build rather than describing live data.

#### Deploying

Foundry IQ's OneLake ingestion is not ETL-free in the sense of "no
Azure AI Search involved" — it still provisions and runs a real
Search index, just without you hand-building the ingestion/chunking
pipeline. See
[Microsoft Learn](https://learn.microsoft.com/en-us/fabric/onelake/onelake-foundry-knowledge)
and
[the OneLake-files-indexer how-to](https://learn.microsoft.com/en-us/azure/search/search-how-to-index-onelake-files).

Deployed and verified live, fully automated:

`deploy_kb_files.py` uploads `00`–`03` to the dimension Lakehouse's
`Files/kb/` folder (same OneLake mechanism
`fabric/ontology/deploy_dimension_lakehouse.py` uses for the
dimension CSVs, just without a "Load Table" step — these are
documents, not tabular data). `deploy_search_indexer.py` configures
the full Azure AI Search chain: a OneLake files data source, an index
(with a semantic configuration), an indexer, a `searchIndex`-kind
knowledge source wrapping the index, and — since it's just another
Search data-plane object (`PUT .../knowledgebases/{name}`,
`api-version=2026-05-01-preview`) — the Foundry IQ **knowledge base**
itself, with `outputMode: "extractiveData"` and
`retrievalReasoningEffort: "minimal"` (the reasoning tier that needs
no attached model deployment). All wired into `infra/fabric.tf` as
`null_resource`s and run on `terraform apply` — see §2's Foundry IQ
knowledge base section for the requirements involved. Confirmed live:
`4/4` docs indexed, a test query for "overdue invoice" correctly
surfaces `03-erp-orders.md`, and the knowledge base answers questions
correctly in the Foundry portal.

The Search service is registered as a **Connected resource** on the
Foundry project, and the knowledge base is exposed as a `RemoteTool`
MCP connection an agent can call, both via `azapi_resource` in
`infra/azure.tf` (no native `azurerm` resource covers this).

#### Extending

Keep new KB documents narrow and specific — glossary entries,
thresholds, "why" explanations, business rules — not restatements of
what a table's columns already say. A KB doc earns its place when it
answers a question the schema alone can't (e.g. "what counts as
overdue," "what does CrystalFormIndex actually measure").

## 6. Query the demo agent

```bash
TOKEN=$(az account get-access-token --scope https://ai.azure.com/.default --query accessToken -o tsv)
curl -s "<AZURE_FOUNDRY_ACCOUNT_ENDPOINT>/api/projects/chocolate-factory/openai/v1/responses" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"agent_reference": {"type": "agent_reference", "name": "chocolate-factory-agent"}, "input": [{"role": "user", "content": "<your question>"}]}'
```

`<AZURE_FOUNDRY_ACCOUNT_ENDPOINT>` is a `terraform output` from
`infra`. Or use the Foundry portal playground directly. Either way,
whoever queries the agent needs their own real Fabric workspace role
— not necessarily the same identity that ran `terraform apply` (see
§2's "Reproducing on a different subscription").

The validated walkthrough, run in order:

1. *"What counts as an overdue invoice?"* — `knowledge_base` tool,
   answers with a citation.
2. *"How is my factory going right now?"* — `fabric_data_agent` tool;
   reports defect rate and pass/fail status, and says plainly when it
   can't confirm something rather than guessing.
3. *"Are there any anomalies I should be aware of?"* —
   `fabric_data_agent` tool; names specific downtime anomalies and
   outliers.
4. *"Who are our suppliers?"* then *"Which suppliers provide
   materials, and what type of material does each provide?"* —
   `fabric_iq_ontology` tool.
5. **The centerpiece:** *"Which factories are receiving shipments,
   and how many quality checks failed today at those same
   factories?"* — the agent chains `fabric_iq_ontology` (finds the
   factory via `shipment_to_factory`) into `fabric_data_agent` (using
   that factory as input), then synthesizes both.

Don't ask pronoun-referencing questions cold (*"who are the suppliers
of **this** product?"*) — verified live to fail, since "this product"
has no referent in a fresh turn. Use a concrete noun instead.

### The full verified question bank

Every question the live agent (`chocolate-factory-agent`) can be
asked, organized by which part of the architecture it exercises. Each
is marked:

- **✅ Verified** — run live against the deployed stack, exact result
  summarized. Safe to use as-is.
- **🧪 Candidate** — plausible given the real data model
  (`fabric/ontology/ontology_config.json`), not yet run live. Test
  before relying on it in front of a room.
- **❌ Known to fail** — run live and failed, with the diagnosed
  reason. Useful for the "known gaps" beat in
  [`README.md`](README.md#known-limitations), not for the main
  script.

Summary: **11** verified, **8** candidate, **4** known to fail.

#### Foundry IQ knowledge base (`knowledge_base` tool)

Grounds in `foundry/kb/00-company-overview.md` through
`03-erp-orders.md`. Best for policy/definition questions a schema
alone can't answer.

- ✅ *"What counts as an overdue invoice?"* — correct definition (past
  `DueDate`, no `PaidDate`), citation to `03-erp-orders.md`.
- ✅ *"What does CrystalFormIndex measure?"* — correct (a
  tempering-stage quality metric, target Form V), citation to
  `01-factory-quality.md`. Good because this metric exists **only**
  in the KB doc, not in any table schema — proves the KB earns its
  place.
- 🧪 *"Why can't a nib shortage always be substituted the way a
  packaging shortage can?"* — content exists in `02-supply-chain.md`;
  not yet run live.
- 🧪 *"What's our company's chocolate percentage range across
  recipes?"* — content exists in `00-company-overview.md`.

#### Fabric Data Agent (`fabric_data_agent` tool)

Grounds in the Eventhouse (`silver_quality_check`,
`silver_line_status`). Best for live telemetry aggregates — this is
the tool that proves the demo is running against real, moving data,
not a snapshot.

- ✅ *"How many quality checks failed today?"* — correctly reports
  the count (0 in the verified run, all passing).
- ✅ *"How is my factory going right now?"* — reports defect
  rate/pass-fail status correctly, and **explicitly says** it can't
  confirm live line-running status from available data rather than
  guessing. Worth narrating: that honesty is the point.
- ✅ *"Are there any anomalies I should be aware of?"* — the
  strongest single-tool answer verified so far: named downtime
  anomalies (50%+ down-events on some lines), recurring reasons
  (changeover, scheduled maintenance, unplanned stops), and a
  specific outlier called out by name ("Molding & Cooling" fail-rate
  spike on LATAM-GRU Line 2).
- 🧪 *"Which line has the most downtime today?"*
- 🧪 *"What's the average defect rate by stage this week?"*

#### Fabric IQ Ontology (`fabric_iq_ontology` tool)

Grounds in the 22-entity, 27-relationship ontology spanning
Factory/Quality and Supply Chain/ERP. Best for structural/relationship
questions — this is the "semantic layer as a real queryable graph,
not a diagram" beat.

- ✅ *"What entity types exist in the ontology?"* — correctly
  enumerates all 22 across both domains.
- ✅ *"Who are our suppliers?"* — correctly lists all 9 real
  suppliers by name.
- ✅ *"Which suppliers provide materials, and what type of material
  does each provide?"* — correct supplier↔material-type pairings for
  all 9.
- 🧪 *"Which factory does production line X belong to?"*
  (`line_to_factory` relationship, real data).
- 🧪 *"What shipments is factory FAC-CHI receiving?"*
  (`shipment_to_factory`, real data — this is half of the centerpiece
  question below, worth testing standalone too).

#### The centerpiece — cross-domain reasoning (2 tools, chained)

The actual evidence that one agent can reason across specialized
backends, not a claim. Verified live: the second tool call's argument
depends on the first call's result.

- ✅ *"Which factories are receiving shipments, and how many quality
  checks failed today at those same factories?"* — calls
  `fabric_iq_ontology` first (finds a factory receiving a shipment
  via `shipment_to_factory`), then calls `fabric_data_agent` **using
  that specific factory as input**, then synthesizes both into one
  correlated answer. If the demo surface can show the raw response
  JSON, the `mcp_call` entries (`server_label`, `arguments`) are
  worth projecting.
- 🧪 *"Which factory has the worst quality this week, and do we have
  enough inventory of its key material to keep it running?"* — a more
  ambitious version of the same pattern, spanning all 3
  tools/domains. Not yet tested; likely to hit the known
  material/recipe gap below if phrased around a specific product
  rather than a factory. Test before using live.

#### Known to fail — useful for the honesty beat, not the main script

- ❌ *"Who are the suppliers of **this** product?"* — fails with
  `search_ontology: query could not be processed`. Root cause,
  isolated by retesting with clearer phrasing: **"this product" has
  no referent in a single fresh turn** — not a data gap. Rephrasing
  to "our suppliers" or a named recipe works fine (see ✅ above).
- ❌ (by data-model inspection, not yet run) *"Which suppliers fed the
  batches in Dark 70% production?"* or similar batch-to-material
  traceability — no `recipe`/`batch` → `material`/`supplier`
  relationship exists in the ontology at all. Real gap, not a bug:
  the data to support batch-level material genealogy was never
  generated on either the simulator or seed-data side. See
  `fabric/ontology/generate_fabric_iq_definition.py`'s docstring.
- ❌ (by design, not yet run) anything requiring `batch_to_line` or
  `batch_to_recipe` relationship data — both are type-only (no real
  instance data), because their source `silver_batch` is a
  materialized view and OneLake mirroring doesn't support
  materialized views yet.

#### Multi-agent architecture (A2A) — not currently demoable

No live questions here: a Coordinator + 2-specialist-agent
architecture was built and torn down in a timeboxed spike, after
confirming the underlying A2A protocol works but the product's
`a2a_preview` tool invocation path doesn't, reproducibly — see
[`README.md`'s "what we tried and didn't make the cut"](README.md#the-demo)
section for how to present this honestly. The centerpiece question
above is the actual evidence for "multi-agent reasoning" the talk can
show live today.

## 7. One-time manual step: Operations Agent

`terraform apply` provisions the Operations Agent item
(`chocolate_factory_predictive_maintenance`) with its instructions,
data source, and alert rule already configured, plus the Activator
item (`chocolate-factory-operations-agent-connector`,
`FABRIC_OPERATIONS_AGENT_CONNECTOR_ID` output) that stores the alert
action's Power Automate connection. Two things can't be done via
Terraform or the REST API and need to be done once, by hand, in the
Fabric portal: confirming the Kusto data source is connected, and
connecting the alert action to a real Power Automate flow.

### Why this can't be automated

Two genuine platform bugs were found live while wiring this up,
confirmed by isolating each with raw REST calls against the Fabric
API directly (bypassing Terraform) rather than assumed from the
Terraform error alone:

- **An `OntologyDefinitions` entry in the `playbook` that isn't
  referenced by any `RuleDefinition` (via a `RuleCondition` or an
  `ActionBinding` parameter) makes item creation fail with a
  `500 InternalError`**, or hang until the provider's create timeout
  (raised to 30m here) is exceeded. Not documented anywhere found via
  Microsoft Learn/GitHub search. The fix is structural, not
  configurable: every property defined in `OntologyDefinitions` must
  be referenced somewhere in `RuleDefinitions`.
- **`dataSources[].id` and `shouldRun` are only reliably applied on
  the item's initial creation call.** Re-pushing the same, valid
  definition via `updateDefinition` — which is what a routine
  `terraform apply` does once the resource already exists in state —
  silently resets `dataSources[].id` to all-zeros and `shouldRun` to
  `false`, leaving the agent `Inactive` with a broken data-source
  binding. Confirmed by fetching the item's live definition via
  `getDefinition` after both a fresh `POST .../items` (correct) and
  an `updateDefinition` call with byte-identical content (broken) —
  not a one-off, reproduced twice. `infra/fabric.tf`'s resource block
  sets `lifecycle { ignore_changes = [definition] }` specifically to
  stop any future `terraform apply` from ever re-pushing a definition
  update to this resource and re-triggering the bug.

Because of the second bug, **this resource cannot be made to reach a
working, running state through the API alone.** `fabric_activator.
operations_agent_connector` provisions the Activator item the alert
action's Power Automate connection is stored on, but the connection
itself and the flow it triggers are portal/Power-Automate-maker-only
— no REST or Terraform surface exists for either. Finish the setup by
hand once, following the steps below.

### 1. Connect the data source

1. Open the **chocolate_factory_predictive_maintenance** Operations
   Agent item in the Fabric workspace.
2. In the agent setup, confirm the **temperingTelemetry** data source
   points at the workspace's Eventhouse KQL database
   (`chocolate-factory-kql`). If it shows as disconnected, reconnect
   it to that KQL database.

### 2. Connect the SendTemperingAlert action to a Power Automate flow

1. Open the Operations Agent item's **Agent setup**, find the
   **SendTemperingAlert** action, and select **Edit**.
2. Select the workspace and the
   **chocolate-factory-operations-agent-connector** Activator item
   (already provisioned by `terraform apply`).
3. Select **Copy** to copy the connection string.
4. Select **Open flow builder** — this opens Power Automate in a new
   tab with the correct trigger already in place.
5. In the flow builder, paste the connection string into the
   trigger's **Connection string** field and select **Save**.
6. Add one action to the flow, for example:
   - **Post message in a chat or channel** (Teams), or
   - **Send an email (V2)**
7. In that action, use **dynamic content** to insert the `batchId`
   and `crystalFormIndex` values passed by the agent into the message
   body/subject.
8. Save the flow.
9. Back in the Fabric portal, the **SendTemperingAlert** action
   should now show as **Connected**.

### 3. Turn the agent on

In the agent setup, confirm **shouldRun** is enabled (the agent
should show as **Active**, not **Inactive**).

### 4. Verify

Run the simulator with anomaly injection targeting the tempering
stage's `CrystalFormIndex` (see §3's "Scenario variants" —
`--anomaly-rate 1.0`) and confirm the configured message (Teams or
email, per step 2.6 above) arrives within the agent's evaluation
cadence (~5 minutes). Repeat for 3 consecutive clean runs before
treating this as demo-ready.

## 8. Pausing when done

A paid Fabric capacity bills per-minute while `Active`. Nightly
auto-pause is on by default (`fabric_capacity_auto_pause_enabled`,
20:00 UTC), or pause manually when you're done for the day:

```bash
uv run fabric/manage_capacity.py pause
```
