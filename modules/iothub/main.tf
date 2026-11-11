resource "azurerm_iothub" "iothub" {
  name                = "iot-${var.app_name}-${var.env_prefix}"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags

  sku {
    name     = "F1" # Free tier for the pilot (8,000 messages/day)[cite: 1]
    capacity = 1
  }

  # The built-in 'events' endpoint is generated automatically.
  # Custom custom endpoint blocks are only needed if routing to external Blob/Service Bus.
}

# Stream IoT Hub platform logs to the central workspace[cite: 1]
resource "azurerm_monitor_diagnostic_setting" "iothub_diag" {
  name                       = "diag-iot-${var.env_prefix}"
  target_resource_id         = azurerm_iothub.iothub.id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log {
    category = "Connections"
  }
  enabled_log {
    category = "DeviceTelemetry"
  }
  metric {
    category = "AllMetrics"
    enabled  = true
  }
}
