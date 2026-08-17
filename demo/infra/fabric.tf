# Fabric workspace + items for the chocolate factory demo. Container
# resources only (workspace, Eventhouse, KQL Database, Eventstream) --
# the actual Bronze/Silver/Gold KQL (demo/eventhouse/01-03) stays a manual
# step. fabric_kql_database's `definition` attribute takes a
# DatabaseSchema.kql-shaped bundle, and whether that format tolerates
# things like `.alter table policy streamingingestion` and
# `.create materialized-view` wasn't something this pass could verify
# against a live Fabric tenant -- safer to keep that step explicit than
# guess.

# ---------------------------------------------------------------------
# Capacity (only resolved when creating a new workspace -- an existing
# workspace already has one, which is exactly the trial-capacity
# workaround; see variables_fabric.tf).
# ---------------------------------------------------------------------

data "fabric_capacity" "this" {
  count = var.use_existing_workspace ? 0 : 1

  id           = var.capacity_id != "" ? var.capacity_id : null
  display_name = var.capacity_id == "" ? var.capacity_display_name : null

  lifecycle {
    postcondition {
      condition     = self.state == "Active"
      error_message = "Fabric Capacity is not in Active state."
    }
  }
}

# ---------------------------------------------------------------------
# Workspace -- create new, or reference existing (trial-tenant path).
# ---------------------------------------------------------------------

resource "fabric_workspace" "this" {
  count = var.use_existing_workspace ? 0 : 1

  display_name                   = var.new_workspace_display_name
  description                    = "Chocolate factory demo -- Factory/Quality telemetry Eventhouse"
  capacity_id                    = data.fabric_capacity.this[0].id
  skip_capacity_state_validation = var.skip_capacity_state_validation

  identity = var.enable_workspace_identity ? { type = "SystemAssigned" } : null
}

data "fabric_workspace" "existing" {
  count = var.use_existing_workspace ? 1 : 0

  id                             = var.existing_workspace_id != "" ? var.existing_workspace_id : null
  display_name                   = var.existing_workspace_id == "" ? var.existing_workspace_display_name : null
  skip_capacity_state_validation = var.skip_capacity_state_validation
}

locals {
  workspace_id = var.use_existing_workspace ? data.fabric_workspace.existing[0].id : fabric_workspace.this[0].id

  # Feeds workspace_identity_principal_id (main.tf / variables.tf) so the
  # Event Hub Data Receiver role assignment can be wired up automatically
  # when workspace identity is available -- whichever path it came from.
  workspace_identity_service_principal_id = var.use_existing_workspace ? try(data.fabric_workspace.existing[0].identity.service_principal_id, "") : try(fabric_workspace.this[0].identity.service_principal_id, "")
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
# Eventstream -- reuses demo/eventhouse/eventstream.json as-is, filling
# its placeholders via TextReplace rather than editing the file (so it
# stays portable/manually-usable outside Terraform too). Skipped entirely
# if no existing Event Hub connection was supplied.
# ---------------------------------------------------------------------

data "fabric_connection" "event_hub" {
  count = var.existing_event_hub_connection_id != "" ? 1 : 0

  id = var.existing_event_hub_connection_id
}

resource "fabric_eventstream" "this" {
  count = var.existing_event_hub_connection_id != "" ? 1 : 0

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
          value = data.fabric_connection.event_hub[0].id
        },
        {
          type  = "TextReplace"
          find  = "<WORKSPACE_ID>"
          value = local.workspace_id
        },
        {
          type  = "TextReplace"
          find  = "<EVENTHOUSE_ITEM_ID>"
          value = fabric_eventhouse.this.id
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
