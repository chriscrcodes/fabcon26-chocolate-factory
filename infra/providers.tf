terraform {
  # >= 1.8 required by the fabric provider itself.
  required_version = ">= 1.8"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.2"
    }
    fabric = {
      source  = "microsoft/fabric"
      version = "~> 1.0"
    }
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.0"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.11"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
    external = {
      source  = "hashicorp/external"
      version = "~> 2.3"
    }
  }
}

provider "azurerm" {
  subscription_id = var.subscription_id

  # Application Insights auto-provisions two companion resources
  # (a "Smart Detection" action group and a "Failure Anomalies" smart
  # detector alert rule) as a side effect, outside any resource block
  # here -- Terraform never tracks them in state, so a normal destroy
  # can't remove them, and the default containment check then refuses
  # to delete the resource group while they're still in it. Confirmed
  # live: destroying everything else first, then trying to delete the
  # resource group, failed with exactly this. false lets Terraform
  # delete the resource group directly via the Azure API instead of
  # checking its contents first, clearing up these orphans too.
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
}

# Defaults to Azure CLI auth (`az login`) -- same as azurerm above, no
# separate auth setup needed. See
# https://registry.terraform.io/providers/microsoft/fabric/latest/docs
# for service-principal options if that's ever needed instead.
#
# preview = true is required for fabric_operations_agent (fabric.tf) --
# a preview-only resource per its own docs, which also note it needs
# user-context auth (no service principal support); this provider
# already runs as the deploying user's own Azure CLI session, so that
# constraint is already satisfied by how this repo runs Terraform.
provider "fabric" {
  preview = true
}

# Provisions Microsoft.Fabric/capacities (fabric.tf) -- an ARM resource
# not exposed by azurerm or the fabric provider. Defaults to Azure CLI
# auth like the two providers above.
provider "azapi" {
  subscription_id = var.subscription_id
}
