# Fabric side of the demo: the capacity itself, a dedicated workspace on
# it, and the workspace items (Eventhouse, KQL Database, Connection,
# Eventstream). fabric_kql_database's `definition` attribute takes a
# DatabaseSchema.kql-shaped bundle, and whether that format tolerates
# things like `.alter table policy streamingingestion` and
# `.create materialized-view` wasn't something this pass could verify
# against a live Fabric tenant -- safer to keep that step explicit than
# guess, so the Bronze/Silver/Gold KQL (fabric/eventhouse/01-03) stays a
# manual step run against the database created below.

variable "skip_capacity_state_validation" {
  description = <<-EOT
    Skip verifying the workspace's capacity is Active. Defaults to true --
    covers the timing gap between the capacity being accepted by ARM
    (below) and it becoming visible/Active through Fabric's own
    capacity-listing API, and is also needed for anyone whose principal
    lacks capacity-listing permission on a shared capacity.
  EOT
  type        = bool
  default     = true
}

variable "new_workspace_display_name" {
  description = "Display name of the Fabric workspace created on the capacity."
  type        = string
  default     = "Chocolate Factory"
}

variable "fabric_capacity_sku" {
  description = "Microsoft.Fabric/capacities SKU."
  type        = string
  default     = "F2"

  validation {
    condition     = contains(["F2", "F4", "F8", "F16", "F32", "F64", "F128", "F256", "F512", "F1024", "F2048"], var.fabric_capacity_sku)
    error_message = "fabric_capacity_sku must be one of the Fabric F-SKUs (F2-F2048)."
  }
}

variable "fabric_capacity_admin_members" {
  description = "Azure AD UPNs or object IDs granted Fabric capacity admin on the new capacity. At least one is required by Azure."
  type        = list(string)

  validation {
    condition     = length(var.fabric_capacity_admin_members) > 0
    error_message = "fabric_capacity_admin_members must contain at least one UPN or object ID."
  }
}

variable "fabric_capacity_name" {
  description = "Name of the Microsoft.Fabric/capacities resource. Defaults to a deterministic name derived from name_prefix and the resource group, same pattern as azure.tf's event_hub_namespace_name."
  type        = string
  default     = ""
}

variable "enable_workspace_identity" {
  description = <<-EOT
    Sets identity = SystemAssigned on the new workspace, so its
    service_principal_id can feed workspace_identity_principal_id
    (azure.tf) for the Event Hub Data Receiver role, closing the loop
    documented in README.
  EOT
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------
# Capacity -- the one piece of the Fabric side that IS a real ARM
# resource (Microsoft.Fabric/capacities), so it's provisioned via azapi
# rather than the microsoft/fabric provider (which can only look it up,
# via data.fabric_capacity below).
# ---------------------------------------------------------------------

locals {
  # local.suffix comes from azure.tf -- reused here so the capacity name
  # stays deterministic and unique per RG/prefix without a stored
  # random_id resource, same reasoning as event_hub_namespace_name.
  fabric_capacity_name = var.fabric_capacity_name != "" ? var.fabric_capacity_name : "fab${var.name_prefix}${local.suffix}"
}

resource "azapi_resource" "fabric_capacity" {
  type      = "Microsoft.Fabric/capacities@2023-11-01"
  name      = local.fabric_capacity_name
  parent_id = local.resource_group_id
  location  = var.location

  body = {
    sku = {
      name = var.fabric_capacity_sku
      tier = "Fabric"
    }
    properties = {
      administration = {
        members = var.fabric_capacity_admin_members
      }
    }
  }

  response_export_values = ["properties.state"]

  tags = var.tags
}

# ARM accepting the capacity doesn't guarantee it's immediately visible
# through Fabric's own capacity-listing API -- which is what
# data.fabric_capacity's "state == Active" postcondition (below) checks.
# This gap wasn't measurable against a live tenant during this pass; if
# `terraform apply` fails on that postcondition, re-running after a short
# wait (or raising create_duration below) is the fix.
resource "time_sleep" "capacity_ready" {
  depends_on      = [azapi_resource.fabric_capacity]
  create_duration = "60s"
}

# ---------------------------------------------------------------------
# Nightly auto-pause -- suspends the Fabric capacity on a schedule so
# leaving it running overnight/over a weekend (its single biggest cost
# risk, since it's a flat per-minute charge while Active regardless of
# whether anything is actually using it) doesn't depend on remembering
# to do it manually. Resume stays a manual step
# (fabric/manage_capacity.py resume) since demo/rehearsal timing is
# irregular -- auto-resuming on a fixed schedule would either resume
# too early (paying for idle time before a session) or too late
# (blocking on a cold capacity right when it's needed).
#
# An Azure Automation Account + PowerShell runbook, not a Python one:
# Automation's PowerShell runtime has the Az module preinstalled and
# authenticates via the account's own managed identity with a single
# `Connect-AzAccount -Identity`; a Python runbook needs its own package
# management step (azurerm_automation_python3_package) for
# azure-identity/requests, more moving parts for the same result.
# Automation Accounts include 500 free minutes/month on every tier --
# one ~5-second suspend call per night costs nothing.
#
# Schedule runs in UTC, not the demo's local timezone -- Terraform's
# date functions don't do IANA timezone conversion, and hand-rolling a
# fixed UTC-offset guess would silently drift wrong across DST. Fixed
# UTC time is honest about that limitation rather than pretending
# precision it doesn't have; being off by an hour or two on when
# "evening" starts doesn't matter for what this is protecting against.
# ---------------------------------------------------------------------

variable "fabric_capacity_auto_pause_enabled" {
  description = "Provision an Azure Automation runbook that suspends the Fabric capacity nightly, to avoid paying for idle compute between demo/rehearsal sessions."
  type        = bool
  default     = true
}

variable "fabric_capacity_auto_pause_time_utc" {
  description = "UTC time (HH:mm, 24h) the nightly auto-pause runbook fires."
  type        = string
  default     = "20:00"
}

resource "azurerm_automation_account" "fabric_capacity" {
  count = var.fabric_capacity_auto_pause_enabled ? 1 : 0

  name                = "aa-${var.name_prefix}-${local.suffix}"
  resource_group_name = local.resource_group_name
  location            = var.location
  sku_name            = "Basic"

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

# Scoped to just this one capacity, not the resource group -- least
# privilege for an identity whose only job is calling one action on
# one resource. "Contributor" is broader than strictly needed (Azure
# has no built-in role scoped to just the suspend/resume actions), but
# scoping it to a single resource keeps the blast radius small.
resource "azurerm_role_assignment" "fabric_capacity_auto_pause_contributor" {
  count = var.fabric_capacity_auto_pause_enabled ? 1 : 0

  scope                = azapi_resource.fabric_capacity.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_automation_account.fabric_capacity[0].identity[0].principal_id
}

resource "azurerm_automation_variable_string" "fabric_capacity_id" {
  count = var.fabric_capacity_auto_pause_enabled ? 1 : 0

  name                    = "FabricCapacityId"
  resource_group_name     = local.resource_group_name
  automation_account_name = azurerm_automation_account.fabric_capacity[0].name
  value                   = azapi_resource.fabric_capacity.id
}

resource "azurerm_automation_runbook" "pause_fabric_capacity" {
  count = var.fabric_capacity_auto_pause_enabled ? 1 : 0

  name                    = "PauseFabricCapacity"
  resource_group_name     = local.resource_group_name
  location                = var.location
  automation_account_name = azurerm_automation_account.fabric_capacity[0].name
  runbook_type            = "PowerShell"
  log_progress            = true
  log_verbose             = true

  content = <<-EOT
    Connect-AzAccount -Identity | Out-Null
    $capacityId = Get-AutomationVariable -Name 'FabricCapacityId'
    Write-Output "Suspending $capacityId"
    Invoke-AzRestMethod -Path "$($capacityId)/suspend?api-version=2023-11-01" -Method POST
  EOT

  tags = var.tags
}

resource "azurerm_automation_schedule" "nightly_pause" {
  count = var.fabric_capacity_auto_pause_enabled ? 1 : 0

  name                    = "nightly-fabric-capacity-pause"
  resource_group_name     = local.resource_group_name
  automation_account_name = azurerm_automation_account.fabric_capacity[0].name
  frequency               = "Day"
  interval                = 1
  timezone                = "Etc/UTC" # Azure normalizes "UTC" to this internally -- matching it avoids a perpetual diff.
  # Anchored to "tomorrow" at apply time so it's always in the future
  # (Azure rejects a past start_time); ignore_changes freezes it after
  # first creation so re-applying doesn't perpetually drift/recreate
  # this on every plan just because timestamp() advances.
  start_time = "${formatdate("YYYY-MM-DD", timeadd(timestamp(), "24h"))}T${var.fabric_capacity_auto_pause_time_utc}:00Z"

  lifecycle {
    ignore_changes = [start_time]
  }
}

resource "azurerm_automation_job_schedule" "nightly_pause" {
  count = var.fabric_capacity_auto_pause_enabled ? 1 : 0

  resource_group_name     = local.resource_group_name
  automation_account_name = azurerm_automation_account.fabric_capacity[0].name
  schedule_name           = azurerm_automation_schedule.nightly_pause[0].name
  runbook_name            = azurerm_automation_runbook.pause_fabric_capacity[0].name
}

# Looked up here by display_name since that's the value we control
# directly, rather than guessing at how the Fabric-side capacity ID maps
# to the ARM resource ID.
data "fabric_capacity" "this" {
  display_name = azapi_resource.fabric_capacity.name

  depends_on = [time_sleep.capacity_ready]

  lifecycle {
    postcondition {
      # Deliberately NOT gated by var.skip_capacity_state_validation
      # (that variable exists for the narrower ARM-vs-Fabric-visibility
      # race right after creation, covered by time_sleep.capacity_ready
      # above -- not for a deliberately-paused capacity). Confirmed
      # live and the hard way: when the capacity is Paused, the
      # `fabric` provider can't read the workspace's items at all, and
      # `terraform plan` responds by showing the *already-existing*
      # Eventhouse/KQL database/Lakehouse/SQL database as "will be
      # created" -- a real risk of destroying and recreating the whole
      # Fabric data estate if that plan were ever applied without
      # noticing. Failing loudly and early here, before any of that
      # gets a chance to plan, is much safer than a silent skip.
      condition     = self.state == "Active"
      error_message = "Fabric Capacity is not Active (state: ${self.state}). If this is the nightly auto-pause, run `fabric/manage_capacity.py resume` first -- do not proceed with plan/apply while paused, since Fabric items become unreadable and can show as needing recreation."
    }
  }
}

# ---------------------------------------------------------------------
# Workspace -- dedicated workspace on the capacity above.
# ---------------------------------------------------------------------

resource "fabric_workspace" "this" {
  display_name                   = var.new_workspace_display_name
  description                    = "Chocolate factory demo -- Factory/Quality telemetry Eventhouse"
  capacity_id                    = data.fabric_capacity.this.id
  skip_capacity_state_validation = var.skip_capacity_state_validation

  identity = var.enable_workspace_identity ? { type = "SystemAssigned" } : null
}

locals {
  workspace_id = fabric_workspace.this.id

  # Feeds workspace_identity_principal_id (azure.tf) so the Event Hub
  # Data Receiver role assignment can be wired up automatically when
  # workspace identity is enabled.
  workspace_identity_service_principal_id = try(fabric_workspace.this.identity.service_principal_id, "")
}

# ---------------------------------------------------------------------
# Eventhouse + KQL Database
# ---------------------------------------------------------------------

resource "fabric_eventhouse" "this" {
  display_name = "chocolate-factory-eventhouse"
  workspace_id = local.workspace_id
}

resource "fabric_kql_database" "this" {
  display_name = "chocolate-factory-kql"
  workspace_id = local.workspace_id

  configuration = {
    database_type = "ReadWrite"
    eventhouse_id = fabric_eventhouse.this.id
  }
}

# ---------------------------------------------------------------------
# Lakehouse -- holds the 4 dimension tables (factory, production_line,
# production_stage, recipe) as real Delta tables, loaded from
# fabric/ontology/tables/*.csv. These are genuine dimension data with no
# timestamp column, so Fabric IQ Ontology's Eventhouse binding
# (TimeSeries-only, verified live) can't cover them -- only
# LakehouseTableDataBindingProperties can, hence this item.
# ---------------------------------------------------------------------

resource "fabric_lakehouse" "dimensions" {
  # Lakehouse names, like Ontology names, reject hyphens -- verified
  # live ("DisplayName is Invalid for ArtifactType").
  display_name = "chocolate_factory_dimensions"
  workspace_id = local.workspace_id
}

# ---------------------------------------------------------------------
# Foundry IQ knowledge base source files -- foundry/kb/*.md uploaded to the
# same dimension Lakehouse's Files area (Files/kb/, alongside
# Files/dimensions/), so an Azure AI Search OneLake files indexer (see
# foundry/kb/deploy_search_indexer.py) has something to index. No "Load
# Table" step -- these are unstructured docs, not tabular data.
# ---------------------------------------------------------------------

resource "null_resource" "load_kb_files" {
  depends_on = [fabric_lakehouse.dimensions]

  triggers = {
    files_hash = sha256(join("", [
      for f in [
        "${path.module}/../foundry/kb/00-company-overview.md",
        "${path.module}/../foundry/kb/01-factory-quality.md",
        "${path.module}/../foundry/kb/02-supply-chain.md",
        "${path.module}/../foundry/kb/03-erp-orders.md",
        "${path.module}/../foundry/kb/deploy_kb_files.py",
      ] : filesha256(f)
    ]))
  }

  provisioner "local-exec" {
    command = "uv run --with azure-identity --with azure-storage-file-datalake ${path.module}/../foundry/kb/deploy_kb_files.py"

    environment = {
      FABRIC_WORKSPACE_ID = local.workspace_id
      FABRIC_LAKEHOUSE_ID = fabric_lakehouse.dimensions.id
    }
  }
}

# Grants the Azure AI Search service's system-assigned managed identity
# (azure.tf) access to this workspace, so its OneLake files indexer can
# list/read Files/kb/ -- Contributor is the documented *minimum* role
# for a search service identity (Viewer is not sufficient), per
# https://learn.microsoft.com/en-us/azure/search/search-how-to-index-onelake-files's
# "Grant permissions" section.
resource "fabric_workspace_role_assignment" "search_contributor" {
  workspace_id = local.workspace_id

  principal = {
    id   = azurerm_search_service.kb.identity[0].principal_id
    type = "ServicePrincipal"
  }
  role = "Contributor"
}

# Configures the Search-side plumbing (data source + index + indexer)
# that indexes Files/kb/ straight out of OneLake -- the Foundry IQ
# knowledge base itself, on top of this index, has no documented
# Terraform/REST path yet and stays a manual portal step (see
# SETUP.md's foundry/kb section).
resource "null_resource" "deploy_search_indexer" {
  depends_on = [
    null_resource.load_kb_files,
    fabric_workspace_role_assignment.search_contributor,
    azurerm_role_assignment.search_foundry_openai_user,
    azurerm_cognitive_deployment.agent_model,
  ]

  triggers = {
    files_hash = filesha256("${path.module}/../foundry/kb/deploy_search_indexer.py")
  }

  provisioner "local-exec" {
    command = "uv run --with requests ${path.module}/../foundry/kb/deploy_search_indexer.py"

    environment = {
      AZURE_SEARCH_ENDPOINT               = "https://${azurerm_search_service.kb.name}.search.windows.net"
      AZURE_SEARCH_ADMIN_KEY              = azurerm_search_service.kb.primary_key
      FABRIC_WORKSPACE_ID                 = local.workspace_id
      FABRIC_LAKEHOUSE_ID                 = fabric_lakehouse.dimensions.id
      AZURE_FOUNDRY_ACCOUNT_ENDPOINT      = azurerm_cognitive_account.foundry.endpoint
      AZURE_FOUNDRY_MODEL_DEPLOYMENT_NAME = azurerm_cognitive_deployment.agent_model.name
    }
  }
}

resource "null_resource" "load_dimension_tables" {
  depends_on = [fabric_lakehouse.dimensions]

  triggers = {
    files_hash = sha256(join("", [
      for f in [
        "${path.module}/../fabric/ontology/tables/factory.csv",
        "${path.module}/../fabric/ontology/tables/production_line.csv",
        "${path.module}/../fabric/ontology/tables/production_stage.csv",
        "${path.module}/../fabric/ontology/tables/recipe.csv",
        "${path.module}/../fabric/ontology/tables/supplier.csv",
        "${path.module}/../fabric/ontology/tables/material.csv",
        "${path.module}/../fabric/ontology/tables/inventory.csv",
        "${path.module}/../fabric/ontology/tables/shipment.csv",
        "${path.module}/../fabric/ontology/tables/customer.csv",
        "${path.module}/../fabric/ontology/tables/product.csv",
        "${path.module}/../fabric/ontology/tables/sales_order.csv",
        "${path.module}/../fabric/ontology/tables/order_line.csv",
        "${path.module}/../fabric/ontology/tables/invoice.csv",
        "${path.module}/../fabric/ontology/deploy_dimension_lakehouse.py",
      ] : filesha256(f)
    ]))
  }

  provisioner "local-exec" {
    command = "uv run --with azure-identity --with azure-storage-file-datalake --with requests ${path.module}/../fabric/ontology/deploy_dimension_lakehouse.py"

    environment = {
      FABRIC_WORKSPACE_ID = local.workspace_id
      FABRIC_LAKEHOUSE_ID = fabric_lakehouse.dimensions.id
    }
  }
}

# ---------------------------------------------------------------------
# Fabric SQL Database -- Supply Chain + ERP/Orders plane (batch/
# transactional business data, not streaming telemetry), per the
# medallion-layers design memo's two-plane design. Created empty by
# Terraform; schema + seed data loaded by
# fabric/sql-database/deploy_sql_database.py (python-tds, no native ODBC
# driver needed) via a local-exec provisioner, same pattern as
# load_kql/load_dimension_tables.
# ---------------------------------------------------------------------

resource "fabric_sql_database" "business" {
  display_name = "chocolate_factory_business"
  workspace_id = local.workspace_id
}

resource "null_resource" "load_business_sql" {
  depends_on = [fabric_sql_database.business]

  triggers = {
    files_hash = sha256(join("", [
      for f in [
        "${path.module}/../fabric/sql-database/01_tables.sql",
        "${path.module}/../fabric/sql-database/02_gold.sql",
        "${path.module}/../fabric/sql-database/deploy_sql_database.py",
        "${path.module}/../fabric/ontology/tables/supplier.csv",
        "${path.module}/../fabric/ontology/tables/material.csv",
        "${path.module}/../fabric/ontology/tables/inventory.csv",
        "${path.module}/../fabric/ontology/tables/shipment.csv",
        "${path.module}/../fabric/ontology/tables/customer.csv",
        "${path.module}/../fabric/ontology/tables/product.csv",
        "${path.module}/../fabric/ontology/tables/sales_order.csv",
        "${path.module}/../fabric/ontology/tables/order_line.csv",
        "${path.module}/../fabric/ontology/tables/invoice.csv",
      ] : filesha256(f)
    ]))
  }

  provisioner "local-exec" {
    command = "uv run --with azure-identity --with python-tds --with certifi --with pyopenssl ${path.module}/../fabric/sql-database/deploy_sql_database.py"

    environment = {
      FABRIC_SQL_SERVER_FQDN   = fabric_sql_database.business.properties.server_fqdn
      FABRIC_SQL_DATABASE_NAME = fabric_sql_database.business.properties.database_name
    }
  }
}

# ---------------------------------------------------------------------
# Connection -- the "cloud connection" from Fabric to the Event Hub
# created in azure.tf. type/creationMethod ("EventHub"/"EventHub.Contents")
# and their required parameters (endpoint, entityPath) come from a live
# call to the Fabric ListSupportedConnectionTypes API
# (https://learn.microsoft.com/en-us/rest/api/fabric/core/connections/list-supported-connection-types)
# against this tenant -- that connector's supportedCredentialTypes are
# OAuth2/Basic/WorkspaceIdentity (no SAS-specific type), and "Basic"
# is what the Fabric UI's "Shared Access Key" auth kind maps to
# (username = SAS policy name, password = SAS key) per
# https://learn.microsoft.com/en-us/fabric/real-time-intelligence/get-data-event-hub.
#
# Two modes, matching azure.tf's local.use_workspace_identity:
#   - workspace identity (enable_workspace_identity = true): the
#     workspace authenticates as itself, already granted Data Receiver
#     in azure.tf.
#   - default: Basic credentials using the listen-only SAS rule from
#     azure.tf (azurerm_eventhub_authorization_rule.eventstream_listen).
# ---------------------------------------------------------------------

resource "fabric_connection" "event_hub" {
  # Fabric connections are unique tenant-wide, not per-workspace (confirmed
  # live: a fixed name here collided with another deployment's connection
  # of the same name in the same tenant) -- suffixed for the same reason
  # local.fabric_capacity_name and local.suffix exist at all.
  display_name      = "chocolate-factory-event-hub-${local.suffix}"
  connectivity_type = "ShareableCloud"

  connection_details = {
    type            = "EventHub"
    creation_method = "EventHub.Contents"
    parameters = [
      {
        name  = "endpoint"
        value = "${azurerm_eventhub_namespace.this.name}.servicebus.windows.net"
      },
      {
        name  = "entityPath"
        value = azurerm_eventhub.this.name
      }
    ]
  }

  credential_details = local.use_workspace_identity ? {
    credential_type   = "WorkspaceIdentity"
    basic_credentials = null
    } : {
    credential_type = "Basic"
    basic_credentials = {
      username            = azurerm_eventhub_authorization_rule.eventstream_listen[0].name
      password_wo         = azurerm_eventhub_authorization_rule.eventstream_listen[0].primary_key
      password_wo_version = 1
    }
  }
}

# ---------------------------------------------------------------------
# Eventstream -- reuses fabric/eventhouse/eventstream.json as-is, filling
# its placeholders via TextReplace rather than editing the file (so it
# stays portable/manually-usable outside Terraform too).
#
# Each destination's "itemId" must be the KQL database's own item ID,
# not the parent Eventhouse's -- verified against a live tenant, where
# passing the Eventhouse ID fails with "Unable to extract cluster URL
# from the Eventhouse KQL database item ID <id>".
# ---------------------------------------------------------------------

resource "fabric_eventstream" "this" {
  display_name = "chocolate-factory-eventstream"
  workspace_id = local.workspace_id
  format       = "Default"

  definition = {
    "eventstream.json" = {
      source          = "${path.module}/../fabric/eventhouse/eventstream.json"
      processing_mode = "Parameters"
      parameters = [
        {
          type  = "TextReplace"
          find  = "<EVENT_HUB_CONNECTION_ID>"
          value = fabric_connection.event_hub.id
        },
        {
          type  = "TextReplace"
          find  = "<WORKSPACE_ID>"
          value = local.workspace_id
        },
        {
          type  = "TextReplace"
          find  = "<KQL_DATABASE_ITEM_ID>"
          value = fabric_kql_database.this.id
        },
        {
          type  = "TextReplace"
          find  = "<KQL_DATABASE_NAME>"
          value = fabric_kql_database.this.display_name
        }
      ]
    }
  }
}

# ---------------------------------------------------------------------
# KQL deployment -- runs fabric/eventhouse/01-03.kql (Bronze/Silver/Gold
# DDL) and seeds the ref_* dimension tables, via a local-exec provisioner.
# No Terraform-native resource covers Kusto control commands here --
# fabric_kql_database's `definition` attribute takes a DatabaseSchema.kql
# bundle, but whether that format tolerates `.alter table policy
# streamingingestion`/`.create-or-alter materialized-view` is unverified
# (see SETUP.md's fabric/eventhouse section); this local-exec path is what was
# actually run and verified against a live tenant. Requires `az login`
# and `uv` on the machine running `terraform apply`.
# ---------------------------------------------------------------------

resource "null_resource" "load_kql" {
  depends_on = [fabric_kql_database.this]

  triggers = {
    files_hash = sha256(join("", [
      for f in [
        "${path.module}/../fabric/eventhouse/01_bronze_and_reference.kql",
        "${path.module}/../fabric/eventhouse/02_silver.kql",
        "${path.module}/../fabric/eventhouse/03_gold.kql",
        "${path.module}/../fabric/eventhouse/04_onelake_mirroring.kql",
        "${path.module}/../fabric/eventhouse/run_kql.py",
      ] : filesha256(f)
    ]))
  }

  provisioner "local-exec" {
    command = "uv run --with azure-kusto-data --with azure-identity ${path.module}/../fabric/eventhouse/run_kql.py ${path.module}/../fabric/eventhouse/01_bronze_and_reference.kql ${path.module}/../fabric/eventhouse/02_silver.kql ${path.module}/../fabric/eventhouse/03_gold.kql ${path.module}/../fabric/eventhouse/04_onelake_mirroring.kql"

    environment = {
      KQL_QUERY_URI = fabric_kql_database.this.properties.query_service_uri
      KQL_DATABASE  = fabric_kql_database.this.display_name
    }
  }
}

# ---------------------------------------------------------------------
# OneLake shortcuts -- lets Eventhouse tables with OneLake availability
# enabled (04_onelake_mirroring.kql, run as part of load_kql above) be
# referenced as ordinary Lakehouse Delta tables, so Fabric IQ Ontology
# relationships whose "from" table is Eventhouse-bound can get real
# Contextualization instances (Eventhouse tables can never be a
# Contextualization *source* directly). See
# fabric/ontology/deploy_onelake_shortcuts.py for which tables and why
# silver_batch (a materialized view) is excluded.
# ---------------------------------------------------------------------

resource "null_resource" "deploy_onelake_shortcuts" {
  depends_on = [null_resource.load_kql, fabric_lakehouse.dimensions]

  triggers = {
    files_hash = filesha256("${path.module}/../fabric/ontology/deploy_onelake_shortcuts.py")
  }

  provisioner "local-exec" {
    command = "uv run --with azure-identity --with requests ${path.module}/../fabric/ontology/deploy_onelake_shortcuts.py"

    environment = {
      FABRIC_WORKSPACE_ID         = local.workspace_id
      FABRIC_LAKEHOUSE_ID         = fabric_lakehouse.dimensions.id
      FABRIC_KQL_DATABASE_ITEM_ID = fabric_kql_database.this.id
    }
  }
}

# ---------------------------------------------------------------------
# Native (non-shortcut) copies of batch/quality_check/line_status in the
# dimension Lakehouse -- the required static-binding source for those
# entities' TimeSeries data, and (via production_line, already native)
# for the 6 sensor_reading_<stage> entities too. See
# fabric/ontology/materialize_static_sources.py's docstring for why the
# OneLake shortcuts above don't qualify (Fabric IQ only accepts
# *managed* Lakehouse tables for static bindings, not external/shortcut
# ones) and why this must re-run on every apply rather than once: unlike
# every other null_resource in this file, `always_run` intentionally
# forces that -- these three source tables keep growing as the
# simulator streams, so a stale copy would silently miss newer
# batches/checks/status events on the next graph refresh.
# ---------------------------------------------------------------------

resource "null_resource" "materialize_static_sources" {
  depends_on = [null_resource.load_kql, fabric_lakehouse.dimensions]

  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = "uv run --with azure-kusto-data --with azure-identity --with azure-storage-file-datalake --with requests ${path.module}/../fabric/ontology/materialize_static_sources.py"

    environment = {
      FABRIC_WORKSPACE_ID = local.workspace_id
      FABRIC_LAKEHOUSE_ID = fabric_lakehouse.dimensions.id
      KQL_QUERY_URI       = fabric_kql_database.this.properties.query_service_uri
      KQL_DATABASE        = fabric_kql_database.this.display_name
    }
  }
}

# ---------------------------------------------------------------------
# Fabric IQ Ontology -- all Factory/Quality entities (batch, quality_check,
# line_status, and 6 per-stage sensor_reading entities bound TimeSeries
# to the Eventhouse; factory, production_line, production_stage, recipe
# bound NonTimeSeries to the dimension Lakehouse above) plus all Supply
# Chain/ERP entities -- see
# fabric/ontology/generate_fabric_iq_definition.py's docstring for the
# full binding rationale and which relationships have instance data
# wired vs. type-only.
#
# Deployed via fabric/ontology/deploy_fabric_iq_ontology.py (direct Fabric
# REST calls), not the `fabric_ontology` Terraform resource -- verified
# live that Terraform-issued create/update calls for this resource
# reliably fail with an opaque ALMOperationImportFailed error against
# this tenant, while byte-identical requests sent directly to the same
# API (same auth, same rendered payload) succeed every time. Same
# reasoning as run_kql.py: don't fight an unreliable path once a
# verified-working alternative exists.
# ---------------------------------------------------------------------

resource "null_resource" "deploy_ontology" {
  depends_on = [
    null_resource.load_kql,
    null_resource.load_dimension_tables,
    null_resource.deploy_onelake_shortcuts,
    null_resource.materialize_static_sources,
  ]

  triggers = {
    files_hash = sha256(join("", [
      for f in concat(
        [for f in fileset("${path.module}/../fabric/ontology/fabric_iq", "**") : "${path.module}/../fabric/ontology/fabric_iq/${f}"],
        ["${path.module}/../fabric/ontology/deploy_fabric_iq_ontology.py"]
      ) : filesha256(f)
    ]))
  }

  provisioner "local-exec" {
    command = "uv run --with azure-identity --with requests ${path.module}/../fabric/ontology/deploy_fabric_iq_ontology.py"

    environment = {
      FABRIC_WORKSPACE_ID         = local.workspace_id
      FABRIC_KQL_DATABASE_ITEM_ID = fabric_kql_database.this.id
      FABRIC_CLUSTER_URI          = fabric_kql_database.this.properties.query_service_uri
      FABRIC_KQL_DATABASE_NAME    = fabric_kql_database.this.display_name
      FABRIC_LAKEHOUSE_ID         = fabric_lakehouse.dimensions.id
    }
  }
}

# Looks up the Ontology item's ID by display name via a live Fabric
# REST call -- there's no Terraform-native resource/attribute for it
# since the item itself is created out-of-band by
# null_resource.deploy_ontology's local-exec script, not a Terraform
# resource. No Terraform resource currently consumes this (the Fabric
# IQ Ontology's Foundry connection needs delegated/BYO-Entra auth,
# created manually through the Foundry portal -- see
# foundry/agents/deploy_foundry_agent.py's docstring), but the ID is
# needed by hand for that manual step, so it's surfaced as a plain
# output below rather than left undiscoverable.
data "external" "ontology_item" {
  depends_on = [null_resource.deploy_ontology]
  program    = ["uv", "run", "--with", "azure-identity", "--with", "requests", "${path.module}/../fabric/ontology/get_ontology_item_id.py"]
  query = {
    workspace_id = local.workspace_id
  }
}

output "FABRIC_ONTOLOGY_ITEM_ID" {
  description = "Fabric IQ Ontology item ID -- {itemId} in the Ontology MCP endpoint URL, needed for the manual Foundry portal connection (Fabric IQ / Microsoft Fabric tile)."
  value       = data.external.ontology_item.result.id
}

output "FABRIC_CAPACITY_ID" {
  description = "Microsoft.Fabric/capacities ARM resource ID."
  value       = azapi_resource.fabric_capacity.id
}

output "FABRIC_WORKSPACE_ID" {
  description = "Fabric workspace ID -- <WORKSPACE_ID> in fabric/eventhouse/eventstream.json"
  value       = local.workspace_id
}

output "FABRIC_EVENTHOUSE_ID" {
  description = "Eventhouse item ID."
  value       = fabric_eventhouse.this.id
}

output "FABRIC_KQL_DATABASE_NAME" {
  description = "KQL database name -- <KQL_DATABASE_NAME> in fabric/eventhouse/eventstream.json. Run fabric/eventhouse/01-03 against this database."
  value       = fabric_kql_database.this.display_name
}

output "FABRIC_KQL_DATABASE_ITEM_ID" {
  description = "KQL database item ID -- <KQL_DATABASE_ITEM_ID> in fabric/eventhouse/eventstream.json (destinations' itemId must be the database's own item, not the parent Eventhouse's)."
  value       = fabric_kql_database.this.id
}

output "FABRIC_KQL_DATABASE_QUERY_URI" {
  description = "KQL database query URI -- KQL_QUERY_URI for fabric/eventhouse/run_kql.py when running it by hand."
  value       = fabric_kql_database.this.properties.query_service_uri
}

output "FABRIC_CONNECTION_ID" {
  description = "Fabric Connection ID for the Event Hub cloud connection."
  value       = fabric_connection.event_hub.id
}

output "FABRIC_EVENTSTREAM_ID" {
  description = "Eventstream item ID."
  value       = fabric_eventstream.this.id
}

output "FABRIC_WORKSPACE_IDENTITY_ENABLED" {
  description = "Whether the workspace has an identity Terraform could read."
  value       = local.workspace_identity_service_principal_id != ""
}

output "FABRIC_LAKEHOUSE_ID" {
  description = "Dimension Lakehouse item ID."
  value       = fabric_lakehouse.dimensions.id
}

output "FABRIC_SQL_DATABASE_ID" {
  description = "Supply Chain/ERP Fabric SQL Database item ID."
  value       = fabric_sql_database.business.id
}

output "FABRIC_SQL_SERVER_FQDN" {
  description = "Fabric SQL Database server FQDN -- FABRIC_SQL_SERVER_FQDN for fabric/sql-database/deploy_sql_database.py when running it by hand."
  value       = fabric_sql_database.business.properties.server_fqdn
}

output "FABRIC_SQL_DATABASE_NAME" {
  description = "Fabric SQL Database name -- FABRIC_SQL_DATABASE_NAME for fabric/sql-database/deploy_sql_database.py when running it by hand."
  value       = fabric_sql_database.business.properties.database_name
}

# ---------------------------------------------------------------------
# Fabric Data Agent -- an NL-to-query agent grounded in the Eventhouse
# (Factory/Quality). Schema verified against
# https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/data-agent-definition.
# Originally scoped as one shared agent spanning both the Eventhouse and
# a Lakehouse mirror of the Supply Chain/ERP tables too (avoiding a
# separate data agent per domain), but the Lakehouse leg never became
# queryable despite five live-tried configurations -- including an
# exact reproduction of a config built through the Fabric portal's own
# "+ Add data source" picker, confirmed live in the portal's own Sources
# view as connected with no warning, and STILL failing identically both
# via this agent's MCP endpoint and the portal's own native chat panel
# ("data sources ... do not contain supplier information"). Concluded
# this is a current product limitation of Data Agent + Lakehouse Tables
# for this data shape, not a configuration mistake -- see
# SETUP.md's foundry/agents section for the full sequence tried. Supply
# Chain/ERP grounding for the Foundry agent comes from the Ontology
# (already covers all 9 tables with 27 real relationships) instead.
# ---------------------------------------------------------------------

resource "fabric_data_agent" "business" {
  depends_on = [null_resource.load_dimension_tables]

  display_name = "chocolate_factory_data_agent"
  workspace_id = local.workspace_id
  format       = "Default"

  definition = {
    "Files/Config/data_agent.json" = {
      source = "${path.module}/../fabric/data-agent/data_agent.json.tmpl"
    }
    "Files/Config/draft/stage_config.json" = {
      source = "${path.module}/../fabric/data-agent/draft/stage_config.json.tmpl"
    }
    "Files/Config/draft/kusto-eventhouse/datasource.json" = {
      source = "${path.module}/../fabric/data-agent/draft/kusto-eventhouse/datasource.json.tmpl"
      tokens = {
        WorkspaceId   = local.workspace_id
        KqlDatabaseId = fabric_kql_database.this.id
      }
    }
  }
}

# A draft-only Data Agent 404s "while enumerating tools" for any
# caller -- confirmed live on a fresh redeploy in a second resource
# group. This step was missing from every previous apply and had only
# ever been run by hand against the original deployment; not
# reproducible without it.
resource "null_resource" "publish_data_agent" {
  depends_on = [fabric_data_agent.business]

  triggers = {
    data_agent_id = fabric_data_agent.business.id
  }

  provisioner "local-exec" {
    command = "uv run --with azure-identity --with requests ${path.module}/../fabric/data-agent/publish_data_agent.py"

    environment = {
      FABRIC_WORKSPACE_ID  = local.workspace_id
      FABRIC_DATA_AGENT_ID = fabric_data_agent.business.id
    }
  }
}

output "FABRIC_DATA_AGENT_ID" {
  description = "Shared Fabric Data Agent item ID (Eventhouse + Fabric SQL Database)."
  value       = fabric_data_agent.business.id
}

# ---------------------------------------------------------------------
# Operations Agent -- a Fabric Real-Time Intelligence item, distinct
# from the Data Agent above: not a conversational Q&A tool, but an
# LLM-configured monitoring/alerting agent that continuously evaluates
# its own instructions against a live data source and notifies when
# conditions are met (https://learn.microsoft.com/en-us/fabric/real-time-intelligence/operations-agent).
# Monitors the tempering stage's CrystalFormIndex (silver_tempering,
# not currently used by the Data Agent above) as a predictive-quality
# signal -- catching drift at the tempering stage rather than waiting
# for the downstream quality check, per foundry/kb/01-factory-quality.md's
# existing description of what CrystalFormIndex measures.
#
# Preview resource: needs `preview = true` on the provider (providers.tf)
# and, per its own docs, doesn't support service-principal auth --
# already satisfied since this repo's Terraform always runs under the
# deploying user's own Azure CLI session, never a service principal.
# Schema confirmed from
# https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/operations-agent-definition
# (not documented on the Terraform resource page itself).
# ---------------------------------------------------------------------

variable "operations_agent_recipient_upn" {
  description = "UPN the Operations Agent sends Teams alerts to. Defaults to the first fabric_capacity_admin_members entry if left empty."
  type        = string
  default     = ""
}

locals {
  operations_agent_recipient_upn = var.operations_agent_recipient_upn != "" ? var.operations_agent_recipient_upn : var.fabric_capacity_admin_members[0]
}

# Stores the SendTemperingAlert action's Power Automate connection
# string -- see SETUP.md's Operations Agent section for the one-time manual
# steps (connection string + flow) this repo can't automate: nothing in
# the fabric_operations_agent definition schema references this item,
# the link only exists on the Fabric portal/Power Automate side.
resource "fabric_activator" "operations_agent_connector" {
  display_name = "chocolate-factory-operations-agent-connector"
  workspace_id = local.workspace_id
}

resource "fabric_operations_agent" "predictive_maintenance" {
  depends_on = [null_resource.load_kql]

  display_name = "chocolate_factory_predictive_maintenance"
  workspace_id = local.workspace_id
  format       = "Default"

  definition = {
    "Configurations.json" = {
      source = "${path.module}/../fabric/operations-agent/Configurations.json.tmpl"
      tokens = {
        WorkspaceId   = local.workspace_id
        KqlDatabaseId = fabric_kql_database.this.id
        RecipientUpn  = local.operations_agent_recipient_upn
      }
    }
  }

  timeouts = {
    create = "30m"
  }

  # This preview API's dataSources[].id and shouldRun are only reliably
  # honored on the item's *initial* create call -- confirmed live that
  # re-pushing the same definition via updateDefinition (what a routine
  # `terraform apply` would do once this resource already exists) silently
  # resets dataSources[].id to all-zeros and shouldRun to false, leaving
  # the agent Inactive with a broken data-source binding. Terraform must
  # never push a definition update to this resource after initial
  # creation -- see SETUP.md's Operations Agent section for the
  # one-time manual portal finish-up this requires instead.
  lifecycle {
    ignore_changes = [definition]
  }
}

output "FABRIC_OPERATIONS_AGENT_ID" {
  description = "Predictive-maintenance Operations Agent item ID."
  value       = fabric_operations_agent.predictive_maintenance.id
}

output "FABRIC_OPERATIONS_AGENT_CONNECTOR_ID" {
  description = "Activator item used to store SendTemperingAlert's Power Automate connection -- see SETUP.md's Operations Agent section."
  value       = fabric_activator.operations_agent_connector.id
}

