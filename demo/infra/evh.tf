# Event Hub Namespace + Event Hub that demo/data-generation streams to,
# and that demo/eventhouse/eventstream.json reads from as its
# AzureEventHub source. See README for the auth-mode tradeoff.

data "azurerm_resource_group" "this" {
  name = var.resource_group_name
}

locals {
  # An explicit workspace_identity_principal_id wins (e.g. Fabric-side
  # resources managed elsewhere); otherwise fall back to whatever
  # fabric.tf resolved from the workspace actually created/referenced in
  # this same apply -- see its workspace_identity_service_principal_id.
  effective_workspace_identity_principal_id = var.workspace_identity_principal_id != "" ? var.workspace_identity_principal_id : local.workspace_identity_service_principal_id
  use_workspace_identity                    = local.effective_workspace_identity_principal_id != ""

  # Deterministic suffix from stable inputs -- avoids a stored random_id
  # resource while still keeping names globally unique per RG/prefix pair.
  suffix                   = substr(md5("${data.azurerm_resource_group.this.id}-${var.name_prefix}"), 0, 8)
  event_hub_namespace_name = "evhns-${var.name_prefix}-${local.suffix}"
  event_hub_name           = "evh-${var.name_prefix}-telemetry"
}

resource "azurerm_eventhub_namespace" "this" {
  name                = local.event_hub_namespace_name
  location            = var.location
  resource_group_name = data.azurerm_resource_group.this.name
  sku                 = var.sku_name
  capacity            = var.sku_capacity

  # Local auth (SAS) is required for the Data Sender demo/SAS flow this repo
  # has actually been tested against; disabled only once a workspace
  # identity takes over (workspace_identity_principal_id set).
  local_authentication_enabled = !local.use_workspace_identity

  tags = var.tags
}

resource "azurerm_eventhub" "this" {
  name              = local.event_hub_name
  namespace_id      = azurerm_eventhub_namespace.this.id
  partition_count   = var.partition_count
  message_retention = var.message_retention_days
}

resource "azurerm_role_assignment" "user_data_sender" {
  scope                = azurerm_eventhub.this.id
  role_definition_name = "Azure Event Hubs Data Sender"
  principal_id         = var.user_object_id
}

resource "azurerm_role_assignment" "workspace_identity_data_receiver" {
  count = local.use_workspace_identity ? 1 : 0

  scope                = azurerm_eventhub.this.id
  role_definition_name = "Azure Event Hubs Data Receiver"
  principal_id         = local.effective_workspace_identity_principal_id
}
