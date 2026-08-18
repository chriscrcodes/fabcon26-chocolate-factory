terraform {
  # >= 1.8 required by the fabric provider itself.
  required_version = ">= 1.8"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
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
  }
}

provider "azurerm" {
  features {}
}

# Defaults to Azure CLI auth (`az login`) -- same as azurerm above, no
# separate auth setup needed. See
# https://registry.terraform.io/providers/microsoft/fabric/latest/docs
# for service-principal options if that's ever needed instead.
provider "fabric" {}

# Provisions Microsoft.Fabric/capacities (capacity.tf) -- an ARM resource
# not exposed by azurerm or the fabric provider. Defaults to Azure CLI
# auth like the two providers above.
provider "azapi" {}
