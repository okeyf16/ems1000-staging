provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = true
    }
    key_vault {
      purge_soft_delete_on_destroy    = true
      recover_soft_deleted_key_vaults = true
    }
  }
}
   terraform {
     required_providers {
       random = {
         source  = "hashicorp/random"
         version = "~> 3.6"
       }
     }
   }
