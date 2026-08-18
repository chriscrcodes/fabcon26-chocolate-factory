# Fabric-side variables. capacity.tf provisions the Fabric capacity itself
# (Azure-provisioned only -- trial capacities are a documented, unsupported
# limitation of the microsoft/fabric provider); fabric.tf creates a
# dedicated workspace on it plus the Eventhouse/KQL Database/Eventstream
# items.

variable "skip_capacity_state_validation" {
  description = <<-EOT
    Skip verifying the workspace's capacity is Active. Defaults to true --
    covers the timing gap between the capacity being accepted by ARM
    (capacity.tf) and it becoming visible/Active through Fabric's own
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
  description = "Name of the Microsoft.Fabric/capacities resource. Defaults to a deterministic name derived from name_prefix and the resource group, same pattern as evh.tf's event_hub_namespace_name."
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

