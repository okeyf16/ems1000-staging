resource "azurerm_service_plan" "app_plan" {
  name                = "plan-api-${var.app_name}-${var.env_prefix}"
  resource_group_name = var.resource_group_name
  location            = var.location
  os_type             = "Linux"
  sku_name            = var.app_service_sku
  tags                = var.tags
}

resource "azurerm_linux_web_app" "web_app" {
  name                = "app-${var.app_name}-${var.env_prefix}-${var.name_suffix}"
  resource_group_name = var.resource_group_name
  location            = var.location
  service_plan_id     = azurerm_service_plan.app_plan.id
  https_only          = true
  tags                = var.tags

  site_config {
    # Must be false on F1 Free tier; automatically enables when upgraded to B1+
    always_on         = var.app_service_sku != "F1"
    ftps_state        = "Disabled"
    health_check_path = "/healthz"
    # Gunicorn managing Uvicorn workers: Microsoft's documented FastAPI startup on App Service
    app_command_line = "gunicorn -w 2 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 main:app"

    application_stack {
      python_version = "3.11"
    }
  }

  app_settings = {
    "SCM_DO_BUILD_DURING_DEPLOYMENT"        = "true"
    "APPLICATIONINSIGHTS_CONNECTION_STRING" = var.app_insights_connection_string
    "OTEL_SERVICE_NAME"                     = "ems-api"
    "STORAGE_CONN_STR"                      = var.storage_primary_connection_string
    "HISTORY_TABLE_NAME"                    = var.history_table_name
    "IOTHUB_CONNECTION_STRING"              = "HostName=iot-${var.app_name}-${var.env_prefix}.azure-devices.net;SharedAccessKeyName=iothubowner;SharedAccessKey=${var.iothub_owner_key}"
  }
}

# Route HTTP and Console logs to Log Analytics (prevents filling F1's 1 GB disk)
resource "azurerm_monitor_diagnostic_setting" "webapp_diag" {
  name                       = "diag-api-${var.env_prefix}"
  target_resource_id         = azurerm_linux_web_app.web_app.id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log {
    category = "AppServiceHTTPLogs"
  }
  enabled_log {
    category = "AppServiceConsoleLogs"
  }
  metric {
    category = "AllMetrics"
    enabled  = true
  }
}
