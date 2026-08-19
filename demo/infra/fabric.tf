# Fabric side of the demo: the capacity itself, a dedicated workspace on
# it, and the workspace items (Eventhouse, KQL Database, Connection,
# Eventstream). fabric_kql_database's `definition` attribute takes a
# DatabaseSchema.kql-shaped bundle, and whether that format tolerates
# things like `.alter table policy streamingingestion` and
# `.create materialized-view` wasn't something this pass could verify
# against a live Fabric tenant -- safer to keep that step explicit than
# guess, so the Bronze/Silver/Gold KQL (demo/eventhouse/01-03) stays a
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
  parent_id = data.azurerm_resource_group.this.id
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

# Looked up here by display_name since that's the value we control
# directly, rather than guessing at how the Fabric-side capacity ID maps
# to the ARM resource ID.
data "fabric_capacity" "this" {
  display_name = azapi_resource.fabric_capacity.name

  depends_on = [time_sleep.capacity_ready]

  lifecycle {
    postcondition {
      condition     = self.state == "Active"
      error_message = "Fabric Capacity is not in Active state."
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
# demo/ontology/tables/*.csv. These are genuine dimension data with no
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

resource "null_resource" "load_dimension_tables" {
  depends_on = [fabric_lakehouse.dimensions]

  triggers = {
    files_hash = sha256(join("", [
      for f in [
        "${path.module}/../ontology/tables/factory.csv",
        "${path.module}/../ontology/tables/production_line.csv",
        "${path.module}/../ontology/tables/production_stage.csv",
        "${path.module}/../ontology/tables/recipe.csv",
        "${path.module}/../ontology/deploy_dimension_lakehouse.py",
      ] : filesha256(f)
    ]))
  }

  provisioner "local-exec" {
    command = "uv run --with azure-identity --with azure-storage-file-datalake --with requests ${path.module}/../ontology/deploy_dimension_lakehouse.py"

    environment = {
      FABRIC_WORKSPACE_ID = local.workspace_id
      FABRIC_LAKEHOUSE_ID = fabric_lakehouse.dimensions.id
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
  display_name      = "chocolate-factory-event-hub"
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
# Eventstream -- reuses demo/eventhouse/eventstream.json as-is, filling
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
      source          = "${path.module}/../eventhouse/eventstream.json"
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
# KQL deployment -- runs demo/eventhouse/01-03.kql (Bronze/Silver/Gold
# DDL) and seeds the ref_* dimension tables, via a local-exec provisioner.
# No Terraform-native resource covers Kusto control commands here --
# fabric_kql_database's `definition` attribute takes a DatabaseSchema.kql
# bundle, but whether that format tolerates `.alter table policy
# streamingingestion`/`.create-or-alter materialized-view` is unverified
# (see demo/eventhouse/README.md); this local-exec path is what was
# actually run and verified against a live tenant. Requires `az login`
# and `uv` on the machine running `terraform apply`.
# ---------------------------------------------------------------------

resource "null_resource" "load_kql" {
  depends_on = [fabric_kql_database.this]

  triggers = {
    files_hash = sha256(join("", [
      for f in [
        "${path.module}/../eventhouse/01_bronze_and_reference.kql",
        "${path.module}/../eventhouse/02_silver.kql",
        "${path.module}/../eventhouse/03_gold.kql",
        "${path.module}/../eventhouse/run_kql.py",
      ] : filesha256(f)
    ]))
  }

  provisioner "local-exec" {
    command = "uv run --with azure-kusto-data --with azure-identity ${path.module}/../eventhouse/run_kql.py ${path.module}/../eventhouse/01_bronze_and_reference.kql ${path.module}/../eventhouse/02_silver.kql ${path.module}/../eventhouse/03_gold.kql"

    environment = {
      KQL_QUERY_URI = fabric_kql_database.this.properties.query_service_uri
      KQL_DATABASE  = fabric_kql_database.this.display_name
    }
  }
}

# ---------------------------------------------------------------------
# Fabric IQ Ontology -- Factory/Quality domain (7 of 8 entities: batch,
# quality_check, line_status bound TimeSeries to the Eventhouse; factory,
# production_line, production_stage, recipe bound NonTimeSeries to the
# dimension Lakehouse above -- see
# demo/ontology/generate_fabric_iq_definition.py's docstring for why
# sensor_reading is still deferred, and which relationships have
# instance data wired vs. type-only).
#
# Deployed via demo/ontology/deploy_fabric_iq_ontology.py (direct Fabric
# REST calls), not the `fabric_ontology` Terraform resource -- verified
# live that Terraform-issued create/update calls for this resource
# reliably fail with an opaque ALMOperationImportFailed error against
# this tenant, while byte-identical requests sent directly to the same
# API (same auth, same rendered payload) succeed every time. Same
# reasoning as run_kql.py: don't fight an unreliable path once a
# verified-working alternative exists.
# ---------------------------------------------------------------------

resource "null_resource" "deploy_ontology" {
  depends_on = [null_resource.load_kql, null_resource.load_dimension_tables]

  triggers = {
    files_hash = sha256(join("", [
      for f in concat(
        [for f in fileset("${path.module}/../ontology/fabric_iq", "**") : "${path.module}/../ontology/fabric_iq/${f}"],
        ["${path.module}/../ontology/deploy_fabric_iq_ontology.py"]
      ) : filesha256(f)
    ]))
  }

  provisioner "local-exec" {
    command = "uv run --with azure-identity --with requests ${path.module}/../ontology/deploy_fabric_iq_ontology.py"

    environment = {
      FABRIC_WORKSPACE_ID         = local.workspace_id
      FABRIC_KQL_DATABASE_ITEM_ID = fabric_kql_database.this.id
      FABRIC_CLUSTER_URI          = fabric_kql_database.this.properties.query_service_uri
      FABRIC_KQL_DATABASE_NAME    = fabric_kql_database.this.display_name
      FABRIC_LAKEHOUSE_ID         = fabric_lakehouse.dimensions.id
    }
  }
}

output "FABRIC_CAPACITY_ID" {
  description = "Microsoft.Fabric/capacities ARM resource ID."
  value       = azapi_resource.fabric_capacity.id
}

output "FABRIC_WORKSPACE_ID" {
  description = "Fabric workspace ID -- <WORKSPACE_ID> in demo/eventhouse/eventstream.json"
  value       = local.workspace_id
}

output "FABRIC_EVENTHOUSE_ID" {
  description = "Eventhouse item ID."
  value       = fabric_eventhouse.this.id
}

output "FABRIC_KQL_DATABASE_NAME" {
  description = "KQL database name -- <KQL_DATABASE_NAME> in demo/eventhouse/eventstream.json. Run demo/eventhouse/01-03 against this database."
  value       = fabric_kql_database.this.display_name
}

output "FABRIC_KQL_DATABASE_ITEM_ID" {
  description = "KQL database item ID -- <KQL_DATABASE_ITEM_ID> in demo/eventhouse/eventstream.json (destinations' itemId must be the database's own item, not the parent Eventhouse's)."
  value       = fabric_kql_database.this.id
}

output "FABRIC_KQL_DATABASE_QUERY_URI" {
  description = "KQL database query URI -- KQL_QUERY_URI for demo/eventhouse/run_kql.py when running it by hand."
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

