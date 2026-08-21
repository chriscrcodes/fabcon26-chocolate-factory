# Azure-side of the demo: the Event Hub Namespace + Event Hub that
# simulator streams to, and that fabric.tf's Fabric Connection
# reads from. See README for the auth-mode tradeoff.

variable "resource_group_name" {
  description = "Name of the resource group to deploy into. Must already exist."
  type        = string
}

variable "location" {
  description = "Azure region for all resources."
  type        = string
  default     = "westeurope"
}

variable "name_prefix" {
  description = "Short prefix used to build resource names (lowercase letters/numbers only)."
  type        = string
  default     = "cacao"

  validation {
    condition     = can(regex("^[a-z0-9]{3,12}$", var.name_prefix))
    error_message = "name_prefix must be 3-12 lowercase letters/numbers."
  }
}

variable "user_object_id" {
  description = "Object ID of the deploying user or service principal -- granted Azure Event Hubs Data Sender. Get with: az ad signed-in-user show --query id -o tsv"
  type        = string
}

variable "workspace_identity_principal_id" {
  description = <<-EOT
    Optional manual override. Principal ID of a Fabric workspace identity --
    when set, it is granted Azure Event Hubs Data Receiver and local (SAS)
    auth is disabled. Usually left empty: fabric.tf resolves this
    automatically from enable_workspace_identity when the Fabric workspace
    in this same apply has identity enabled. Only set this directly if
    managing the Fabric workspace outside this config.
  EOT
  type        = string
  default     = ""
}

variable "partition_count" {
  description = "Event Hub partition count. 10 lines stream concurrently (see simulator/src/seed_data.py) -- one partition per line avoids a single partition becoming a bottleneck."
  type        = number
  default     = 10
}

variable "message_retention_days" {
  description = "Message retention in days. Kept short -- the generator is the replay source, not the Event Hub."
  type        = number
  default     = 1
}

variable "sku_name" {
  description = "Event Hub Namespace SKU."
  type        = string
  default     = "Standard"

  validation {
    condition     = contains(["Basic", "Standard"], var.sku_name)
    error_message = "sku_name must be Basic or Standard."
  }
}

variable "sku_capacity" {
  description = "Throughput units for the Standard SKU."
  type        = number
  default     = 1
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default = {
    Project = "fabcon26-chocolate-factory"
  }
}

data "azurerm_resource_group" "this" {
  name = var.resource_group_name
}

locals {
  # An explicit workspace_identity_principal_id wins (e.g. Fabric-side
  # resources managed elsewhere); otherwise fall back to whatever
  # fabric.tf resolved from the workspace actually created in this same
  # apply -- see its workspace_identity_service_principal_id.
  effective_workspace_identity_principal_id = var.workspace_identity_principal_id != "" ? var.workspace_identity_principal_id : local.workspace_identity_service_principal_id
  use_workspace_identity                    = local.effective_workspace_identity_principal_id != ""

  # Deterministic suffix from stable inputs -- avoids a stored random_id
  # resource while still keeping names globally unique per RG/prefix pair.
  # Reused by fabric.tf for the Fabric capacity name.
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

# Least-privilege (listen-only) SAS policy for the Eventstream connection
# in fabric.tf -- only needed in the SAS/local-auth mode; workspace
# identity mode (above) authenticates as itself instead.
resource "azurerm_eventhub_authorization_rule" "eventstream_listen" {
  count = local.use_workspace_identity ? 0 : 1

  name                = "eventstream-listen"
  namespace_name      = azurerm_eventhub_namespace.this.name
  eventhub_name       = azurerm_eventhub.this.name
  resource_group_name = data.azurerm_resource_group.this.name

  listen = true
  send   = false
  manage = false
}

# Names match simulator's .env.sample so these can be copied
# straight in.

output "AZURE_EVENT_HUB_NAMESPACE_HOSTNAME" {
  description = "Event Hub Namespace hostname -- AZURE_EVENT_HUB_NAMESPACE_HOSTNAME"
  value       = "${azurerm_eventhub_namespace.this.name}.servicebus.windows.net"
}

output "AZURE_EVENT_HUB_NAME" {
  description = "Event Hub name -- AZURE_EVENT_HUB_NAME"
  value       = azurerm_eventhub.this.name
}

output "AZURE_EVENT_HUB_NAMESPACE_ID" {
  description = "Event Hub Namespace resource ID"
  value       = azurerm_eventhub_namespace.this.id
}

output "LOCAL_AUTH_ENABLED" {
  description = "Whether local (SAS) auth is enabled -- false only when workspace_identity_principal_id was supplied"
  value       = !local.use_workspace_identity
}

# ---------------------------------------------------------------------
# Azure AI Search -- backs the Foundry IQ knowledge base over foundry/kb/
# (see foundry/kb/README.md). Foundry IQ's OneLake ingestion still
# provisions/uses a real Search index behind the scenes (it only
# eliminates hand-building the ingestion/chunking pipeline, not Search
# itself -- confirmed against
# https://learn.microsoft.com/en-us/fabric/onelake/onelake-foundry-knowledge),
# so this is a real, billable Azure resource, not a Fabric item.
# System-assigned identity is what the OneLake files indexer
# (foundry/kb/deploy_search_indexer.py) uses to read the Lakehouse's
# Files/kb/ folder -- granted a Fabric workspace role in fabric.tf.
# ---------------------------------------------------------------------

resource "azurerm_search_service" "kb" {
  name                = "srch-${var.name_prefix}-${local.suffix}"
  resource_group_name = data.azurerm_resource_group.this.name
  location            = var.location
  sku                 = "basic"

  # Semantic ranking is disabled by default at the service level, a
  # separate control-plane setting from an index's own
  # semantic.configurations[] block (foundry/kb/deploy_search_indexer.py).
  # Foundry IQ knowledge bases require it: querying one against a
  # service without this set fails with "Knowledge Base requires
  # Semantic Search to be enabled for this service." "free" covers up to
  # 1,000 semantic queries/month at no extra cost -- plenty for a demo.
  semantic_search_sku = "free"

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

output "AZURE_SEARCH_SERVICE_NAME" {
  description = "Azure AI Search service name backing the Foundry IQ knowledge base."
  value       = azurerm_search_service.kb.name
}

output "AZURE_SEARCH_SERVICE_ENDPOINT" {
  description = "Azure AI Search service endpoint -- AZURE_SEARCH_ENDPOINT for foundry/kb/deploy_search_indexer.py."
  value       = "https://${azurerm_search_service.kb.name}.search.windows.net"
}

output "AZURE_SEARCH_PRINCIPAL_ID" {
  description = "Azure AI Search service's system-assigned managed identity principal ID -- granted a Fabric workspace role in fabric.tf so the OneLake files indexer can read Files/kb/."
  value       = azurerm_search_service.kb.identity[0].principal_id
}

# ---------------------------------------------------------------------
# Microsoft Foundry -- project-based (not hub-based), for the Phase 3
# agent spine (foundry/agents/). `azurerm_cognitive_account` with
# `kind = "AIServices"` + `project_management_enabled = true`, paired
# with `azurerm_cognitive_account_project`, is the modern
# project-only shape -- no Key Vault/Storage Account/Hub dependency,
# unlike the older `azurerm_ai_foundry`/`azurerm_ai_foundry_project`
# resource pair (which provisions the legacy hub-based architecture,
# explicitly unsupported for Foundry IQ knowledge sources and other
# newer MCP-tool features this project already depends on). Billed
# per-usage (model calls/deployments), not a flat idle cost like a
# Fabric capacity.
# ---------------------------------------------------------------------

resource "azurerm_cognitive_account" "foundry" {
  name                = "aif-${var.name_prefix}-${local.suffix}"
  resource_group_name = data.azurerm_resource_group.this.name
  location            = var.location
  kind                = "AIServices"
  sku_name            = "S0"

  # Required before project creation -- "Account must set
  # CustomSubDomainName before creating projects" (verified live).
  custom_subdomain_name = "aif-${var.name_prefix}-${local.suffix}"

  project_management_enabled = true

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

resource "azurerm_cognitive_account_project" "chocolate_factory" {
  name                 = "chocolate-factory"
  cognitive_account_id = azurerm_cognitive_account.foundry.id
  location             = azurerm_cognitive_account.foundry.location
  display_name         = "Chocolate Factory"
  description          = "FabCon multi-agent demo -- Coordinator + specialist agents over Fabric IQ Ontology, a Fabric Data Agent, and a Foundry IQ knowledge base."

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

output "AZURE_FOUNDRY_ACCOUNT_NAME" {
  description = "Microsoft Foundry (Cognitive Services AIServices) account name."
  value       = azurerm_cognitive_account.foundry.name
}

output "AZURE_FOUNDRY_ACCOUNT_ENDPOINT" {
  description = "Microsoft Foundry account endpoint."
  value       = azurerm_cognitive_account.foundry.endpoint
}

output "AZURE_FOUNDRY_PROJECT_NAME" {
  description = "Microsoft Foundry project name -- used in the project's own endpoint URL."
  value       = azurerm_cognitive_account_project.chocolate_factory.name
}

output "AZURE_FOUNDRY_PRINCIPAL_ID" {
  description = "Microsoft Foundry account's system-assigned managed identity principal ID."
  value       = azurerm_cognitive_account.foundry.identity[0].principal_id
}
