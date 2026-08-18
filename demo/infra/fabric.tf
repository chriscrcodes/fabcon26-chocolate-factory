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
# Capacity -- provisioned by capacity.tf (azapi); looked up here by
# display_name since that's the value we control directly, rather than
# guessing at how the Fabric-side capacity ID maps to the ARM resource ID.
# ---------------------------------------------------------------------

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

  # Feeds workspace_identity_principal_id (main.tf / variables.tf) so the
  # Event Hub Data Receiver role assignment can be wired up automatically
  # when workspace identity is enabled.
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
# Connection -- the "cloud connection" from Fabric to the Event Hub
# created in evh.tf. type/creationMethod ("EventHub"/"EventHub.Contents")
# and their required parameters (endpoint, entityPath) come from a live
# call to the Fabric ListSupportedConnectionTypes API
# (https://learn.microsoft.com/en-us/rest/api/fabric/core/connections/list-supported-connection-types)
# against this tenant -- that connector's supportedCredentialTypes are
# OAuth2/Basic/WorkspaceIdentity (no SAS-specific type), and "Basic"
# is what the Fabric UI's "Shared Access Key" auth kind maps to
# (username = SAS policy name, password = SAS key) per
# https://learn.microsoft.com/en-us/fabric/real-time-intelligence/get-data-event-hub.
#
# Two modes, matching evh.tf's local.use_workspace_identity:
#   - workspace identity (enable_workspace_identity = true): the
#     workspace authenticates as itself, already granted Data Receiver
#     in evh.tf.
#   - default: Basic credentials using the listen-only SAS rule from
#     evh.tf (azurerm_eventhub_authorization_rule.eventstream_listen).
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
