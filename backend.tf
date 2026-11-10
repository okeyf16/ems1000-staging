terraform {
  cloud {
    # Replace with your actual Terraform Cloud organization name
    organization = "ems1000-staging"

    workspaces {
      name = "ems1000-staging" 
    }
  }

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.90"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  required_version = ">= 1.5.0"
}
