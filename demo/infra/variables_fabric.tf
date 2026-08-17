# Fabric-side variables. See fabric.tf and README's "Trial capacity" note
# before setting these -- the provider's own docs list trial capacities as
# an explicit known limitation, which is what use_existing_workspace exists
# to work around.

variable "use_existing_workspace" {
  description = <<-EOT
    Reference an existing Fabric workspace instead of creating one. Set this
    to true if you're on a Fabric trial -- the provider's own docs say trial
    capacities aren't supported, so a workspace that already sits on one
    can only be referenced, not created/managed through fabric_capacity.
  EOT
  type        = bool
  default     = false
}

variable "existing_workspace_id" {
  description = "Used when use_existing_workspace = true. Either this or existing_workspace_display_name -- not both."
  type        = string
  default     = ""
}

variable "existing_workspace_display_name" {
  description = "Used when use_existing_workspace = true. Either this or existing_workspace_id -- not both."
  type        = string
  default     = ""
}

variable "skip_capacity_state_validation" {
  description = <<-EOT
    Skip verifying the workspace's capacity is Active. Defaults to true --
    this is the documented workaround for trial capacities (the provider
    can't list/validate them), and it's also needed for anyone whose
    principal lacks capacity-listing permission on a shared capacity.
  EOT
  type        = bool
  default     = true
}

variable "new_workspace_display_name" {
  description = "Used when use_existing_workspace = false."
  type        = string
  default     = "Chocolate Factory"
}

variable "capacity_id" {
  description = "Used when use_existing_workspace = false. Either this or capacity_display_name -- not both. Must be an Azure-provisioned capacity (Microsoft.Fabric/capacities) -- trial capacities aren't supported here, see use_existing_workspace."
  type        = string
  default     = ""
}

variable "capacity_display_name" {
  description = "Used when use_existing_workspace = false. Either this or capacity_id -- not both."
  type        = string
  default     = ""
}

variable "enable_workspace_identity" {
  description = <<-EOT
    Only applies when use_existing_workspace = false -- sets identity =
    SystemAssigned on the new workspace, so its service_principal_id can
    feed workspace_identity_principal_id (main.tf) for the Event Hub Data
    Receiver role, closing the loop documented in README. If you're
    referencing an existing workspace instead, enable Workspace Identity
    on it manually first (Workspace settings -> Workspace identity) --
    this repo only reads that state via the data source, it can't set it.
  EOT
  type        = bool
  default     = false
}

variable "existing_event_hub_connection_id" {
  description = <<-EOT
    ID of an existing Fabric Connection to the Event Hub, used as
    eventstream.json's AzureEventHub source. Deliberately not created by
    this config -- the Fabric connector's exact connection_details.type/
    creation_method strings for Azure Event Hubs aren't documented in the
    fabric provider's own reference docs, and guessing them risks a
    silently-wrong connection. Create one via Fabric UI (Eventstream ->
    Add source -> Azure Event Hubs -- see demo/eventhouse/README.md) once,
    then paste its ID here. Leave empty to skip creating the Eventstream
    (Eventhouse + KQL Database still get created).
  EOT
  type        = string
  default     = ""
}
