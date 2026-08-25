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
  features {}
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
