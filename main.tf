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

# Phase 2: Ingestion Layer
module "iothub" {
  source                     = "./modules/iothub"
  resource_group_name        = azurerm_resource_group.rg.name
  location                   = azurerm_resource_group.rg.location
  env_prefix                 = var.env_prefix
  app_name                   = var.app_name
  log_analytics_workspace_id = module.observability.log_analytics_workspace_id
  tags                       = azurerm_resource_group.rg.tags
}

# Phase 3: Compute Layer (Serverless Function)
module "function_app" {
  source                            = "./modules/function_app"
  resource_group_name               = azurerm_resource_group.rg.name
  location                          = azurerm_resource_group.rg.location
  env_prefix                        = var.env_prefix
  app_name                          = var.app_name
  storage_account_name              = module.storage.storage_account_name
  storage_primary_access_key        = module.storage.primary_access_key
  storage_primary_connection_string = module.storage.primary_connection_string
  app_insights_connection_string    = module.observability.app_insights_connection_string
  
  # Inject IoT Hub routing credentials directly into the Function App
  iothub_events_endpoint = module.iothub.eventhub_compatible_endpoint
  iothub_events_path     = module.iothub.eventhub_compatible_path
  iothub_owner_key       = module.iothub.iothub_owner_primary_key
  tags                   = azurerm_resource_group.rg.tags
}
# module "app_service" { ... } 
