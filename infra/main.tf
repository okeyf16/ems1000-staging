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

  # Both alert rules notify this group (defined below, after the API it calls)
  action_group_id = azurerm_monitor_action_group.alerts.id

  missing_telemetry_silence_minutes = var.missing_telemetry_silence_minutes
  missing_telemetry_check_minutes   = var.missing_telemetry_check_minutes

  latency_alert_evaluation_periods = var.latency_alert_evaluation_periods
  latency_alert_failing_periods    = var.latency_alert_failing_periods
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

  # Device connection state and the "Device offline" alert
  alerts_table_name          = module.storage.alerts_table_name
  offline_pending_table_name = module.storage.offline_pending_table_name
  offline_grace_minutes      = var.offline_grace_minutes
  alerts_retention_days      = var.alerts_retention_days
}

# Device connect/disconnect events: IoT Hub -> Event Grid -> the Function's
# device_lifecycle_trigger, which keeps connection_status in DeviceState and raises the
# "Device offline" alert. Event Grid's free grant (100,000 operations a month) covers staging.
resource "azurerm_eventgrid_system_topic" "iothub" {
  name                   = "egst-iothub-${var.app_name}-${var.env_prefix}"
  resource_group_name    = azurerm_resource_group.rg.name
  location               = azurerm_resource_group.rg.location
  source_arm_resource_id = module.iothub.iothub_id
  topic_type             = "Microsoft.Devices.IoTHubs"
  tags                   = azurerm_resource_group.rg.tags
}

resource "azurerm_eventgrid_system_topic_event_subscription" "device_lifecycle" {
  name                = "device-lifecycle-to-function"
  system_topic        = azurerm_eventgrid_system_topic.iothub.name
  resource_group_name = azurerm_resource_group.rg.name

  included_event_types = [
    "Microsoft.Devices.DeviceConnected",
    "Microsoft.Devices.DeviceDisconnected",
  ]

  # The function must already be deployed: Event Grid checks the endpoint when the
  # subscription is created (deploy-function runs on every merge to main).
  azure_function_endpoint {
    function_id                       = "${module.function_app.function_app_id}/functions/device_lifecycle_trigger"
    max_events_per_batch              = 1
    preferred_batch_size_in_kilobytes = 64
  }

  # Connection events are only useful while fresh: retry for up to an hour, then drop.
  retry_policy {
    max_delivery_attempts = 10
    event_time_to_live    = 60
  }
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
  alerts_table_name                 = module.storage.alerts_table_name
  alert_webhook_token               = random_password.alert_webhook_token.result
  api_keys                          = [random_password.api_key["primary"].result, random_password.api_key["secondary"].result]
  docs_password                     = random_password.docs_password.result
}

# Password for the API docs pages (shared with the team; stage 2 moves the docs to
# Microsoft Entra sign-in). Rotate: HCP Terraform -> new run -> replace this resource.
resource "random_password" "docs_password" {
  length  = 24
  special = false
}

# API keys for the X-API-Key header. Two, so one can be replaced while clients use the other:
# HCP Terraform -> new run -> replace random_password.api_key["secondary"], move clients, then primary.
resource "random_password" "api_key" {
  for_each = toset(["primary", "secondary"])
  length   = 40
  special  = false
}

# Alerts reach the simulator panel (and later the mobile app) through the API:
# Azure Monitor calls the API's webhook, which stores them in the Alerts table.
# The token in the URL is the webhook's only credential (Azure Monitor can't send headers).
resource "random_password" "alert_webhook_token" {
  length  = 40
  special = false
}

resource "azurerm_monitor_action_group" "alerts" {
  name                = "ag-${var.app_name}-${var.env_prefix}"
  resource_group_name = azurerm_resource_group.rg.name
  short_name          = "ems-alerts" # max 12 characters, shown in notifications
  tags                = azurerm_resource_group.rg.tags

  webhook_receiver {
    name                    = "api-alerts"
    service_uri             = "https://${module.app_service.web_app_default_hostname}/api/alerts/azure-monitor?token=${random_password.alert_webhook_token.result}"
    use_common_alert_schema = true
  }
}

# IoT Hub daily message quota: warn at each percentage in iothub_quota_alert_percents
# (default 70 % and 90 %) of iothub_daily_message_quota (F1: 8,000, resets 00:00 UTC).
# Same action group as the other alerts, so they show on the panel as System alerts.
# Added after a reconnect loop used the whole free day in minutes (21 Nov 2026).
resource "azurerm_monitor_metric_alert" "iothub_quota" {
  for_each            = toset([for p in var.iothub_quota_alert_percents : tostring(p)])
  name                = "alert-iothub-quota-${each.value}-${var.env_prefix}"
  resource_group_name = azurerm_resource_group.rg.name
  scopes              = [module.iothub.iothub_id]
  description         = "IoT Hub has used ${each.value} % of its ${var.iothub_daily_message_quota} daily messages (resets 00:00 UTC)."
  severity            = tonumber(each.value) >= 90 ? 1 : 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  auto_mitigate       = true
  tags                = azurerm_resource_group.rg.tags

  criteria {
    metric_namespace = "Microsoft.Devices/IotHubs"
    metric_name      = "dailyMessageQuotaUsed"
    aggregation      = "Maximum"
    operator         = "GreaterThanOrEqual"
    threshold        = floor(var.iothub_daily_message_quota * tonumber(each.value) / 100)
  }

  action {
    action_group_id = azurerm_monitor_action_group.alerts.id
  }
}
