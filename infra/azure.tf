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

variable "enable_agent_split" {
  description = <<-EOT
    When false (default), deploy_foundry_agent.py deploys the single
    generalist agent (chocolate-factory-agent) as it always has -- an
    untouched `terraform apply` behaves exactly as before this variable
    existed. When true, it instead deploys the two Prompt-kind
    specialist agents (Factory/Quality, Supply Chain/ERP) plus the
    incoming-A2A setup, and expects the Hosted Coordinator
    (foundry/agents/coordinator/, deployed separately via `azd`, not
    this Terraform state) to take over the chocolate-factory-agent name
    as the public-facing agent. See foundry/agents/README.md and
    ~/.claude/plans/please-analyze-current-project-majestic-meteor.md
    for the fuller design.
  EOT
  type        = bool
  default     = false
}

variable "coordinator_principal_id" {
  description = <<-EOT
    Object ID of the Hosted Coordinator's own managed identity
    (foundry/agents/coordinator/, provisioned separately by `azd
    provision` against its own azd project/environment -- deliberately
    a different Terraform/tooling state from this one). Left empty by
    default because that identity doesn't exist until the Coordinator
    has actually been deployed via azd; only meaningful together with
    enable_agent_split = true. See
    azurerm_role_assignment.coordinator_foundry_agent_consumer's
    comment for why this can't be resolved as a resource reference
    here.
  EOT
  type        = string
  default     = ""
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

  # Undocumented in the provider's own schema (no description string),
  # but live-verified: setting this at all is what flips the service's
  # authOptions from the default apiKeyOnly to aadOrApiKey -- without
  # it, every AAD/RBAC token is rejected regardless of role
  # assignments (this is what silently broke the Foundry IQ knowledge
  # base tool: the project identity already had Search Index Data
  # Reader for 40+ minutes, the role was never the problem, the service
  # just wasn't accepting AAD tokens at all).
  authentication_failure_mode = "http403"

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

# Foundry Agent Service's data-plane API (deploy_foundry_agent.py's
# POST .../agents) is a separate authorization surface from ARM --
# being the deploying user/Owner on the resource group isn't enough.
# Verified live: calling GET .../agents with only that access returns
# `403 ... does not have permissions for
# Microsoft.CognitiveServices/accounts/AIServices/agents/read actions`.
#
# "Azure AI Developer" (the role Microsoft's own docs point to,
# https://learn.microsoft.com/en-us/azure/foundry/concepts/rbac-foundry)
# does NOT actually grant this in this tenant -- checked its live
# definition (`az role definition list --name "Azure AI Developer"`)
# and its dataActions are scoped to OpenAI/SpeechServices/
# ContentSafety/MaaS only, no `AIServices/agents/*` action at all. Of
# the built-in roles with a `Microsoft.CognitiveServices/*` dataActions
# wildcard, "Cognitive Services User" was the first one confirmed to
# work live -- "Azure AI Developer" stayed a 403 for over 10 minutes,
# ruling out propagation delay as the cause. Later switched to "Foundry
# User" instead: same `Microsoft.CognitiveServices/*` dataActions
# (confirmed identical via `az role definition list`), but it's
# Microsoft's current, non-deprecated name for this exact role (the
# Foundry RBAC roles were renamed; "Foundry User" was formerly "Azure
# AI User") -- keeping both around was redundant, not defense in depth,
# since they grant the identical wildcard.
resource "azurerm_role_assignment" "deployer_foundry_user" {
  scope                = azurerm_cognitive_account_project.chocolate_factory.id
  role_definition_name = "Foundry User"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Lets the project's own managed identity query the Foundry IQ
# knowledge base's Azure AI Search index -- the "ProjectManagedIdentity"
# auth path for the RemoteTool/MCP connection below, avoiding an
# admin-key secret the project would otherwise have to store.
resource "azurerm_role_assignment" "foundry_project_search_reader" {
  scope                = azurerm_search_service.kb.id
  role_definition_name = "Search Index Data Reader"
  principal_id         = azurerm_cognitive_account_project.chocolate_factory.identity[0].principal_id
}

# Project connection wiring the Foundry IQ knowledge base's MCP endpoint
# in as a "RemoteTool" the agent can call. No azurerm resource models
# this: azurerm_cognitive_account_connection_* is account-scoped, not
# project-scoped, and its `category` argument is hard-validated to
# ["AIServices", "AzureKeyVault", "AzureOpenAI", "AzureStorageAccount"]
# -- "RemoteTool" is rejected at `terraform validate` time, before any
# API call is even made. azapi_resource calls the ARM connections API
# Microsoft's own docs use directly instead
# (https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/foundry-iq-connect).
resource "azapi_resource" "foundry_iq_kb_connection" {
  type      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview"
  name      = "chocolate-factory-kb"
  parent_id = azurerm_cognitive_account_project.chocolate_factory.id

  # azapi's bundled schema for this preview API version predates
  # "ProjectManagedIdentity" as an authType and rejects it client-side
  # even though it's the value Microsoft's own docs require for this
  # exact scenario (see comment above) -- the live API accepts it fine.
  schema_validation_enabled = false

  body = {
    properties = {
      authType      = "ProjectManagedIdentity"
      category      = "RemoteTool"
      target        = "https://${azurerm_search_service.kb.name}.search.windows.net/knowledgebases/chocolate-factory-kb/mcp?api-version=2026-05-01-preview"
      isSharedToAll = true
      audience      = "https://search.azure.com/"
      metadata = {
        ApiType = "Azure"
      }
    }
  }

  depends_on = [azurerm_role_assignment.foundry_project_search_reader]
}

data "azurerm_client_config" "current" {}

# Project connections wiring the shared Fabric Data Agent and the
# Fabric IQ Ontology in as RemoteTool MCP tools, using delegated
# per-user auth (authType "UserEntraToken") -- genuinely new territory
# this session, arrived at only after two dead ends:
#
# 1. category "RemoteTool" + authType "ProjectManagedIdentity" (the
#    knowledge base's pattern): connects fine, but the Fabric Data
#    Agent itself returns an internal "technical error" for ANY
#    non-interactive identity (confirmed with both this project's
#    managed identity and an independent app-only service-principal
#    token), and the Ontology MCP endpoint has NO application-only
#    auth path at all per Microsoft's docs
#    (https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/fabric-iq)
#    -- both need a real signed-in user's identity, not a service
#    principal.
# 2. category "MicrosoftFabric" + authType "AAD" (reverse-engineered
#    from the Foundry portal's own "Microsoft Fabric" connection
#    wizard, which fails live with a bare 400 if you take its "Custom
#    Keys" field labels at face value -- the real live API error is
#    `"AuthType for MicrosoftFabric Connection can only be AAD,
#    UserEntraToken"`, and workspace-id/artifact-id turned out to be
#    plain `metadata`, not `credentials.keys`): creates cleanly and
#    validates, but every agent query failed with `"Connection
#    resolution failed"` -- this category+authType combination doesn't
#    actually resolve to a usable identity at runtime.
#
# What works, confirmed live end to end (real answers back from both
# the Fabric Data Agent and the Ontology, matching what a direct
# user-token MCP client gets): category "RemoteTool" + authType
# "UserEntraToken" + audience "https://analysis.windows.net/powerbi/api"
# -- the same pattern Microsoft's own docs use for the one concrete
# non-Ontology example they show (a Data Agent behind a workspace
# private link), just applied here without the private-link angle.
# UserEntraToken forwards the calling user's own signed-in identity
# through to Fabric, which is exactly what both tools need and neither
# ProjectManagedIdentity nor AAD provided. Tools reference these with
# the generic `type: "mcp"` shape (same as the knowledge base), not
# `fabric_iq_preview` -- that type is for the MicrosoftFabric-category
# connections that didn't work here.
resource "azapi_resource" "fabric_data_agent_mcp_connection" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview"
  name                      = "fabric-data-agent"
  parent_id                 = azurerm_cognitive_account_project.chocolate_factory.id
  schema_validation_enabled = false

  body = {
    properties = {
      authType      = "UserEntraToken"
      category      = "RemoteTool"
      target        = "https://api.fabric.microsoft.com/v1/mcp/workspaces/${local.workspace_id}/dataagents/${fabric_data_agent.business.id}/agent"
      audience      = "https://analysis.windows.net/powerbi/api"
      isSharedToAll = true
    }
  }
}

resource "azapi_resource" "fabric_iq_ontology_mcp_connection" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview"
  name                      = "fabric-iq-ontology"
  parent_id                 = azurerm_cognitive_account_project.chocolate_factory.id
  schema_validation_enabled = false

  body = {
    properties = {
      authType      = "UserEntraToken"
      category      = "RemoteTool"
      target        = "https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/${local.workspace_id}/items/${data.external.ontology_item.result.id}/ontologyEndpoint"
      audience      = "https://analysis.windows.net/powerbi/api"
      isSharedToAll = true
    }
  }
}

# gpt-5.4-mini -- cheapest Generally Available model at the time of
# deployment (gpt-4o/gpt-4o-mini were both in "Deprecating" lifecycle
# state and rejected live: "ServiceModelDeprecating ... cannot be used
# for new deployments" -- checked `az cognitiveservices account
# list-models` for current GA options rather than assuming an older
# model name still works). Plenty for a demo agent answering grounded
# factual questions rather than doing complex reasoning. GlobalStandard
# SKU, minimum capacity (10 = 10K TPM).
resource "azurerm_cognitive_deployment" "agent_model" {
  name                 = "gpt-5.4-mini"
  cognitive_account_id = azurerm_cognitive_account.foundry.id

  model {
    format  = "OpenAI"
    name    = "gpt-5.4-mini"
    version = "2026-03-17"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 10
  }
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

output "AZURE_FOUNDRY_MODEL_DEPLOYMENT_NAME" {
  description = "Model deployment name to reference when creating an agent."
  value       = azurerm_cognitive_deployment.agent_model.name
}

output "AZURE_FOUNDRY_KB_CONNECTION_NAME" {
  description = "Foundry project connection name for the Foundry IQ knowledge base's MCP tool -- reference this when wiring the connection into an agent's tool list."
  value       = azapi_resource.foundry_iq_kb_connection.name
}

output "AZURE_FOUNDRY_DATA_AGENT_CONNECTION_NAME" {
  description = "Foundry project connection name for the shared Fabric Data Agent's MCP tool."
  value       = azapi_resource.fabric_data_agent_mcp_connection.name
}

output "AZURE_FOUNDRY_ONTOLOGY_CONNECTION_NAME" {
  description = "Foundry project connection name for the Fabric IQ Ontology's MCP tool."
  value       = azapi_resource.fabric_iq_ontology_mcp_connection.name
}

output "AZURE_FOUNDRY_PRINCIPAL_ID" {
  description = "Microsoft Foundry account's system-assigned managed identity principal ID."
  value       = azurerm_cognitive_account.foundry.identity[0].principal_id
}

# Foundry Agent Service agent profiles to deploy via
# deploy_foundry_agent.py, keyed by AGENT_PROFILE -- exactly the shape
# the prior spike's Phase 2 speced
# (~/.claude/plans/now-let-s-deploy-the-reactive-toucan.md) and the
# follow-up plan's Phase 2 confirmed
# (~/.claude/plans/please-analyze-current-project-majestic-meteor.md).
# `enable_agent_split = false` (default) keeps the single map entry
# this file always had -- an untouched `terraform apply` deploys
# exactly the same generalist agent as before this variable existed.
locals {
  agent_deploy_profiles = var.enable_agent_split ? {
    specialist_factory_quality  = { agent_profile = "specialist_factory_quality" }
    specialist_supply_chain_erp = { agent_profile = "specialist_supply_chain_erp" }
    } : {
    generalist = { agent_profile = "generalist" }
  }
}

# Creates/updates the Foundry Agent Service agent(s) above -- like the
# Fabric IQ Ontology and search indexer deployments, this is a direct
# REST call via a script, not a Terraform-native resource: the
# `agents` API isn't modeled by any provider (see
# foundry/agents/deploy_foundry_agent.py's docstring for the full
# tool/connection/auth story). Needs the deploying identity to have
# already been granted "Foundry User" on the project
# (azurerm_role_assignment.deployer_foundry_user) -- that's
# a separate authorization surface from being Owner/Contributor on the
# resource group, and its absence fails with a 403 on
# `AIServices/agents/read`, not a permissions error anyone would
# immediately connect to Terraform.
resource "null_resource" "deploy_foundry_agent" {
  for_each = local.agent_deploy_profiles

  depends_on = [
    azurerm_role_assignment.deployer_foundry_user,
    azurerm_cognitive_deployment.agent_model,
    azapi_resource.foundry_iq_kb_connection,
    azapi_resource.fabric_data_agent_mcp_connection,
    azapi_resource.fabric_iq_ontology_mcp_connection,
  ]

  triggers = {
    files_hash    = filesha256("${path.module}/../foundry/agents/deploy_foundry_agent.py")
    agent_profile = each.value.agent_profile
  }

  provisioner "local-exec" {
    command = "uv run --with azure-identity --with requests ${path.module}/../foundry/agents/deploy_foundry_agent.py"

    environment = {
      AGENT_PROFILE                       = each.value.agent_profile
      AZURE_FOUNDRY_ACCOUNT_NAME          = azurerm_cognitive_account.foundry.name
      AZURE_FOUNDRY_PROJECT_NAME          = azurerm_cognitive_account_project.chocolate_factory.name
      AZURE_FOUNDRY_MODEL_DEPLOYMENT_NAME = azurerm_cognitive_deployment.agent_model.name
      AZURE_SEARCH_SERVICE_NAME           = azurerm_search_service.kb.name
      FABRIC_WORKSPACE_ID                 = local.workspace_id
      FABRIC_DATA_AGENT_ID                = fabric_data_agent.business.id
      FABRIC_ONTOLOGY_ITEM_ID             = data.external.ontology_item.result.id
    }
  }
}

# Enables incoming A2A on the two specialist agents (foundry/agents/deploy_a2a.py)
# -- only meaningful once the specialists exist, so this depends on the
# for_each deploy above rather than any single agent, and is itself
# gated so it's a no-op when the split is off.
resource "null_resource" "deploy_a2a_setup" {
  count = var.enable_agent_split ? 1 : 0

  depends_on = [null_resource.deploy_foundry_agent]

  triggers = {
    files_hash = filesha256("${path.module}/../foundry/agents/deploy_a2a.py")
  }

  provisioner "local-exec" {
    command = "uv run --with azure-identity --with requests ${path.module}/../foundry/agents/deploy_a2a.py"

    environment = {
      AZURE_FOUNDRY_ACCOUNT_NAME = azurerm_cognitive_account.foundry.name
      AZURE_FOUNDRY_PROJECT_NAME = azurerm_cognitive_account_project.chocolate_factory.name
    }
  }
}

# The Hosted Coordinator's own instance identity (foundry/agents/coordinator/)
# needs "Foundry Agent Consumer" on the project to call the two
# specialists' A2A endpoints -- the second of the two grants the prior
# spike found necessary (see
# ~/.claude/plans/now-let-s-deploy-the-reactive-toucan.md's "What got
# fixed/confirmed" section: one grant on the project's own identity,
# already covered by azurerm_role_assignment.deployer_foundry_user's
# sibling concerns, and a second on the *calling* agent's own instance
# identity). That identity is provisioned by `azd provision` against
# the Coordinator's own azd project/environment
# (foundry/agents/coordinator/), a deliberately separate
# Terraform/tooling state from this one -- there is no
# azurerm_/azapi_ resource in *this* state that models it, so its
# principal_id genuinely can't be a resource reference here (inventing
# one, e.g. pointing at the deploying user's or the project's own
# identity, would silently grant the wrong principal rather than fail
# loudly). TODO once the Coordinator is actually deployed via azd:
# resolve its managed identity's object ID (`azd env get-values` /
# `az ad sp show` against the deployed container app's identity) and
# pass it in as `-var coordinator_principal_id=<id>`, or replace this
# with a `data` lookup if/when the identity becomes discoverable by a
# stable name from this state. Until then this resource is a deliberate
# no-op (count = 0) even with enable_agent_split = true.
resource "azurerm_role_assignment" "coordinator_foundry_agent_consumer" {
  count = var.enable_agent_split && var.coordinator_principal_id != "" ? 1 : 0

  scope                = azurerm_cognitive_account_project.chocolate_factory.id
  role_definition_name = "Foundry Agent Consumer"
  principal_id         = var.coordinator_principal_id
}

output "AZURE_FOUNDRY_AGENT_NAME" {
  description = "The Foundry Agent Service agent's name -- pass this in agent_reference when querying it."
  value       = "chocolate-factory-agent"
}
