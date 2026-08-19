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
  Azure Event Hub Namespace + Event Hub + role assignments, that
  `simulator` streams to and `fabric.tf`'s Connection reads
  from
- [`fabric.tf`](fabric.tf) — variables, resources, and outputs for the
  whole Fabric side: the capacity itself (`Microsoft.Fabric/capacities`,
  F2 by default, configurable via `fabric_capacity_sku`, provisioned via
  the `azapi` provider since neither `azurerm` nor `microsoft/fabric`
  expose that ARM resource), a dedicated workspace on it, Eventhouse, KQL
  Database, the Fabric Connection to the Event Hub, the Eventstream item
  itself (built from `fabric/eventhouse/eventstream.json`), a Lakehouse
  holding the ontology's dimension tables *and* the `foundry/kb/*.md`
  knowledge-base docs, a Fabric SQL Database holding the Supply
  Chain/ERP tables, a Fabric IQ Ontology (preview) bound across the
  Eventhouse and Lakehouse, a workspace role assignment granting the
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
dimension Lakehouse, Fabric SQL Database + Supply Chain/ERP data, and
Fabric IQ Ontology, and `simulator`
streams events all the way through to the Gold layer.

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

### Deploy

```bash
cp terraform.tfvars.example terraform.tfvars   # fill in the Azure/Fabric values
terraform init
terraform apply
```

Also requires [`uv`](https://docs.astral.sh/uv/) on the machine running
`terraform apply` — `null_resource.load_kql`'s `local-exec` provisioner
shells out to `uv run --with azure-kusto-data --with azure-identity
../fabric/eventhouse/run_kql.py` to deploy `fabric/eventhouse/01`-`03`'s KQL and
seed the `ref_*` dimension tables from `fabric/ontology/tables/*.csv`
(skipped if already populated, so re-applies don't duplicate rows) —
nothing manual is left after `terraform apply` completes.

If this step fails with `SSLCertVerificationError`, a corporate
TLS-inspecting proxy (e.g. Zscaler) is likely intercepting the
connection to the Kusto endpoint — export `SSL_CERT_FILE` /
`REQUESTS_CA_BUNDLE` pointing at a CA bundle that includes your
org's root CA before running `terraform apply` (a local-machine
fix, not something this config bakes in).

### Capacity and trial-tenant limitation

The `microsoft/fabric` provider's own docs list a **known limitation**:
*"Microsoft Fabric trial capacity is not supported. Only self-provisioned
Fabric Capacity on Azure is supported."* That's why `fabric.tf`
provisions a real Azure-provisioned `Microsoft.Fabric/capacities`
resource via `azapi` (default SKU **F2**, configurable via
`fabric_capacity_sku`) rather than assuming one already exists or
falling back to a trial workspace — there's no supported Terraform path
for the latter.

`fabric.tf` then creates a dedicated workspace on that capacity, and
`enable_workspace_identity = true` sets `identity = { type =
"SystemAssigned" }` on it — the workspace's `service_principal_id` then
feeds the Event Hub Data Receiver role assignment automatically
(`fabric.tf`'s `workspace_identity_service_principal_id` local, read by
`azure.tf`), rather than being a value you'd have to find and paste in by
hand.

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

### Foundry IQ knowledge base — Search-side plumbing only

`azurerm_search_service.kb` (Basic tier), `fabric_workspace_role_assignment.search_contributor`,
`null_resource.load_kb_files`, and `null_resource.deploy_search_indexer`
provision and verify everything up to a queryable Azure AI Search index
over `foundry/kb/*.md` — confirmed live end to end (`4/4` docs indexed,
test query for "overdue invoice" correctly surfaces
`03-erp-orders.md`). Two live-verified fixes needed beyond the
documented happy path:

- The OneLake files indexer's minimum required Fabric workspace role is
  **Contributor**, not Viewer (per
  [Microsoft Learn](https://learn.microsoft.com/en-us/azure/search/search-how-to-index-onelake-files)'s
  "Grant permissions" section) — Viewer was tried first and never
  produced a permissions error, it just silently indexed nothing, so
  don't assume Viewer is "probably fine."
- OneLake's `metadata_storage_path` values are full URLs
  (`https://onelake.blob.fabric.microsoft.com/...`), which contain `:`
  and `/` — invalid characters for a Search index key. First run failed
  every document with "Invalid document key." Fixed with the standard
  blob-indexer `fieldMappings` `base64Encode` function on the key field
  (see `foundry/kb/deploy_search_indexer.py`).

**Not covered by this Terraform state**: the actual Foundry IQ
**knowledge base** object — a Foundry-portal-only step layered on top of
this Search index, with no documented Terraform/CLI/REST path as of this
writing. See `foundry/kb/README.md` for the manual steps and why. A
from-scratch `terraform apply` gets you a ready-to-query Search index;
turning that into something a Foundry agent can call still requires one
manual portal action.
