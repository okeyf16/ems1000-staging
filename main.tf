# Generate a random 4-character string for globally unique resource names
resource "random_string" "suffix" {
  length  = 4
  special = false
  upper   = false
}

# Shared Resource Group for the environment
resource "azurerm_resource_group" "rg" {
  name     = "rg-${var.app_name}-${var.env_prefix}"
  location = var.location
  tags     = merge(var.tags, { Environment = var.env_prefix })
}

# Phase 1: Observability Layer
module "observability" {
  source              = "./modules/observability"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  env_prefix          = var.env_prefix
  daily_quota_gb      = var.daily_quota_gb
  tags                = azurerm_resource_group.rg.tags
}

# Phase 1: Storage Layer
module "storage" {
  source               = "./modules/storage"
  resource_group_name  = azurerm_resource_group.rg.name
  location             = azurerm_resource_group.rg.location
  # Storage accounts allow 3-24 lowercase alphanumeric characters only
  storage_account_name = "st${var.app_name}${var.env_prefix}${random_string.suffix.result}"
  tags                 = azurerm_resource_group.rg.tags
}

# ---------------------------------------------------------
# Phase 2 & 3 Modules (To be added next)
# ---------------------------------------------------------
# module "iothub" { ... }
# module "function_app" { ... }
# module "app_service" { ... }

