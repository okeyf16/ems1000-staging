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
  source                 = "./modules/storage"
  resource_group_name    = azurerm_resource_group.rg.name
  location               = azurerm_resource_group.rg.location
  # Storage accounts allow 3-24 lowercase alphanumeric characters only
  storage_account_name   = "st${var.app_name}${var.env_prefix}${random_string.suffix.result}"
  archive_retention_days = var.archive_retention_days
  tags                   = azurerm_resource_group.rg.tags
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

  # Raw telemetry archive endpoint (blob container in the storage account)
  archive_storage_connection_string = module.storage.primary_connection_string
  archive_container_name            = module.storage.telemetry_archive_container_name
}

# Phase 3: Compute Layer (Serverless Function)
module "function_app" {
  source                            = "./modules/function_app"
  resource_group_name               = azurerm_resource_group.rg.name
  location                          = "Sweden Central"
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

  # Per-message telemetry history
  history_table_name     = module.storage.telemetry_table_name
  history_retention_days = var.history_retention_days
}
# Phase 3: API Layer (FastAPI on Linux App Service F1)
module "app_service" {
  source                            = "./modules/app_service"
  resource_group_name               = azurerm_resource_group.rg.name
  location                          = "France Central"
  env_prefix                        = var.env_prefix
  app_name                          = var.app_name
  name_suffix                       = random_string.suffix.result
  app_service_sku                   = "F1"
  storage_primary_connection_string = module.storage.primary_connection_string
  app_insights_connection_string    = module.observability.app_insights_connection_string
  iothub_owner_key                  = module.iothub.iothub_owner_primary_key
  log_analytics_workspace_id        = module.observability.log_analytics_workspace_id
  tags                              = azurerm_resource_group.rg.tags
  history_table_name                = module.storage.telemetry_table_name
}
