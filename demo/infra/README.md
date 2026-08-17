# infra

Azure-side IaC for the chocolate factory demo, plus what's realistically
IaC-able on the Fabric side today (research below — nothing there is
scripted yet).

## What's here

One Terraform state (not Bicep — see "Why Terraform" below), Azure and
Fabric together:

- [`providers.tf`](providers.tf) — `azurerm` + `microsoft/fabric`
  providers, both defaulting to Azure CLI auth (`az login`), no separate
  setup
- [`evh.tf`](evh.tf), [`variables.tf`](variables.tf),
  [`outputs.tf`](outputs.tf) — Azure Event Hub Namespace + Event Hub +
  role assignments, that `demo/data-generation` streams to and
  `demo/eventhouse/eventstream.json` reads from
- [`fabric.tf`](fabric.tf), [`variables_fabric.tf`](variables_fabric.tf),
  [`outputs_fabric.tf`](outputs_fabric.tf) — Fabric workspace (new or
  existing), Eventhouse, KQL Database, and (optionally) the Eventstream
  item itself, built from `demo/eventhouse/eventstream.json`
- [`terraform.tfvars.example`](terraform.tfvars.example) — copy to
  `terraform.tfvars`; shows both the trial-tenant and real-capacity setups

`terraform validate` passes, and `terraform plan` was run against two
dummy `tfvars` covering both `use_existing_workspace` paths — both
construct the full resource graph correctly and fail only at the
expected point (no `az login` in this environment). Not yet applied to a
real tenant.

Event Hub auth, chosen by whether a workspace identity is available
(`workspace_identity_principal_id`, resolved automatically from
`fabric.tf` when `enable_workspace_identity = true` — see below):

- **Default — SAS, local auth enabled.** The deploying user gets
  **Azure Event Hubs Data Sender**. This is the flow `demo/data-generation`
  has actually been tested against (both its connection-string and
  `AzureCliCredential` code paths work here).
- **Workspace identity available — local auth disabled.** That
  principal gets **Azure Event Hubs Data Receiver** instead, so
  Eventstream authenticates via Entra ID rather than a shared access key
  — see "Eventstream's Event Hub source auth" below.

### Deploy

```bash
cp terraform.tfvars.example terraform.tfvars   # pick a Fabric setup, fill in the Azure values
terraform init
terraform apply
```

Outputs are named to drop straight into `demo/data-generation/.env`
(`AZURE_EVENT_HUB_*`) and `demo/eventhouse/eventstream.json`'s
placeholders (`FABRIC_WORKSPACE_ID`, `FABRIC_EVENTHOUSE_ID`,
`FABRIC_KQL_DATABASE_NAME`) — though if `existing_event_hub_connection_id`
is set, `fabric.tf` fills those placeholders itself and creates the
Eventstream directly; you only need to run `demo/eventhouse/01`-`03`'s
KQL by hand afterward (see "Not attempted here").

### Trial tenant? Use `use_existing_workspace`

The `microsoft/fabric` provider's own docs list a **known limitation**:
*"Microsoft Fabric trial capacity is not supported. Only self-provisioned
Fabric Capacity on Azure is supported."* `fabric_capacity`/`fabric_workspace`
as a **resource** need a real Azure-provisioned capacity
(`Microsoft.Fabric/capacities`) to attach to.

If you're on a trial, set `use_existing_workspace = true` and point it
at a workspace you already created in the Fabric portal (which sits on
the trial capacity) via `existing_workspace_display_name` or
`existing_workspace_id`. This switches to `data "fabric_workspace"` — a
read-only lookup that doesn't touch capacity state at all — and
`skip_capacity_state_validation` (defaults `true`) avoids the capacity
check that would otherwise fail for a trial. Items (Eventhouse, KQL
Database, Eventstream) are then created inside that existing workspace
normally. See `terraform.tfvars.example`'s "Setup A" for the exact
variables.

If you do have a real Azure-provisioned capacity, `use_existing_workspace
= false` (Setup B) creates a fresh workspace on it instead, and
`enable_workspace_identity = true` sets `identity = { type =
"SystemAssigned" }` on it — closing a loop from before: the workspace's
`service_principal_id` now feeds the Event Hub Data Receiver role
assignment automatically (`fabric.tf`'s `workspace_identity_service_principal_id`
local, read by `evh.tf`), rather than being a value you'd have to find
and paste in by hand.

### Why Terraform, not Bicep

Bicep can only reach `Microsoft.Fabric/capacities` (see below) — nothing
else on the Fabric side is an ARM resource, so a Fabric config needs
Terraform's `microsoft/fabric` provider regardless. Keeping the Event Hub
in the same tool means one state, one `apply`. `sources/fabric-data-generation`
itself uses Bicep (its own `event-hub.bicep` was the reference for the
role-assignment pattern in `evh.tf`), so if this demo ever needs to
match that accelerator's deployment convention exactly, reverting that
one file to Bicep is a small, isolated change — everything else in
`demo/` stays independent of that choice either way.

## What can actually be IaC-scripted on the Fabric side

Researched against Microsoft's own docs/repos before writing anything —
summarized here since it shapes what a future `demo/infra` addition
should use, rather than guessed at.

### Bicep/ARM: almost nothing

Only **`Microsoft.Fabric/capacities`** (the underlying Azure capacity SKU
purchase — F2–F2048) is a real ARM/Bicep resource. Workspaces and every
item inside one (Eventhouse, KQL Database, Eventstream, Lakehouse,
Warehouse, SQL Database, Connections...) are **not** ARM resources — they
live in Fabric's own management plane, not Azure Resource Manager's. A
Bicep template can provision the capacity a Fabric tenant runs on; it
cannot provision anything inside a workspace.

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
  earlier version of this doc claimed `demo/eventhouse/01`–`03` could be
  applied through it directly; that's not confirmed, since whether that
  format tolerates `.alter table policy streamingingestion` and
  `.create materialized-view` wasn't verifiable against a live tenant
  here. `fabric.tf` creates the KQL Database as a container only; running
  `01`–`03` stays a manual step (see "Not attempted here")
- `fabric_eventstream` — now in `fabric.tf`, takes
  `demo/eventhouse/eventstream.json` as its definition verbatim, with its
  `<PLACEHOLDER>` tokens filled via `TextReplace` parameters rather than
  editing the file (verified: `processing_mode` must be exactly
  `"Parameters"`, capitalized, and `format = "Default"` is required
  alongside `definition` — both caught by `terraform validate`, not
  obvious from the docs alone)
- `fabric_connection` (data source only, `data.fabric_connection`) — see
  "Not attempted here" for why this repo doesn't *create* the Event Hub
  connection itself
- `fabric_lakehouse`, `fabric_warehouse`, plus domain/role-assignment and
  Spark-environment resources — relevant once Supply Chain/ERP lands in
  `fabric-ontology`'s Fabric SQL DB, not used here yet

Practical implication, and why the Event Hub above is Terraform too:
`Microsoft.Fabric/capacities` has an `azurerm`-adjacent path (AzAPI) that
works fine alongside `microsoft/fabric` in one Terraform state — there's
no need to split Azure and Fabric resources across Bicep and Terraform
when Terraform alone covers both.

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

### Not attempted here

- **Creating the Event Hub connection itself.** `fabric_connection`'s
  schema (`connection_details.type`/`creation_method`) is generic across
  every connector Fabric supports, and the exact strings for "Azure
  Event Hubs" specifically weren't findable in the provider's reference
  docs or examples during this research. Guessing them risks a
  silently-wrong connection that only surfaces as a confusing runtime
  error. `fabric.tf` instead takes `existing_event_hub_connection_id` —
  create the connection once via the Fabric UI (already documented in
  `demo/eventhouse/README.md`'s Eventstream deploy steps), then paste
  its ID in.
- **Running `demo/eventhouse/01`–`03` through Terraform.** See the
  `fabric_kql_database` note above — stays a manual step against the
  database `fabric.tf` creates (`FABRIC_KQL_DATABASE_NAME` output).
