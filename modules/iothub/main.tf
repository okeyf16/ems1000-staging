resource "azurerm_iothub" "iothub" {
  name                = "iot-${var.app_name}-${var.env_prefix}"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags

  sku {
    name     = "F1" # Free tier for the pilot (8,000 messages/day)
    capacity = 1
  }

  # Ensure the built-in endpoint retains messages for at least 1 day[cite: 1]
  endpoint {
    type                       = "AzureIotHub.TelemetryV1"
    connection_string          = "" # Built-in, left empty
    name                       = "events"
    batch_frequency_in_seconds = 60
    max_chunk_size_in_bytes    = 10485760
    encoding                   = "JSON"
  }
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
