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
    automatically from enable_workspace_identity (variables_fabric.tf) when
    the Fabric workspace in this same apply has identity enabled. Only set
    this directly if managing the Fabric workspace outside this config.
  EOT
  type        = string
  default     = ""
}

variable "partition_count" {
  description = "Event Hub partition count. 10 lines stream concurrently (see demo/data-generation/src/seed_data.py) -- one partition per line avoids a single partition becoming a bottleneck."
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
