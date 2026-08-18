# The Fabric capacity itself -- the one piece of the Fabric side that IS
# a real ARM resource (Microsoft.Fabric/capacities), so it's provisioned
# via azapi rather than the microsoft/fabric provider (which can only
# look it up, via data.fabric_capacity in fabric.tf).

locals {
  # local.suffix comes from evh.tf -- reused here so the capacity name
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
# data.fabric_capacity's "state == Active" postcondition (fabric.tf)
# checks. This gap wasn't measurable against a live tenant during this
# pass; if `terraform apply` fails on that postcondition, re-running
# after a short wait (or raising create_duration below) is the fix.
resource "time_sleep" "capacity_ready" {
  depends_on      = [azapi_resource.fabric_capacity]
  create_duration = "60s"
}
