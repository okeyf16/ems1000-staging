resource "azurerm_iothub" "iothub" {
  name                = "iot-${var.app_name}-${var.env_prefix}"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags

  # Must match what Azure reports (TLS 1.2 is now enforced). This attribute is
  # ForceNew in azurerm: leaving it unset made Terraform plan to REPLACE the hub.
  min_tls_version = "1.2"

  sku {
    name     = "F1" # Free tier for the pilot (8,000 messages/day)[cite: 1]
    capacity = 1
  }

  # Replacing the hub would delete every device identity and rotate all keys.
  # Any plan that tries to destroy it now fails instead of applying.
  lifecycle {
    prevent_destroy = true
  }

  # The built-in 'events' endpoint is generated automatically.
  # Custom custom endpoint blocks are only needed if routing to external Blob/Service Bus.
  # Routes are defined as standalone azurerm_iothub_route resources below: never add
  # inline route/endpoint blocks here (changing those can force hub recreation).
}

# Send all device telemetry to the built-in Event Hub-compatible endpoint ('events'),
# which the Function App's IoT Hub trigger reads.
resource "azurerm_iothub_route" "telemetry_to_builtin" {
  name                = "telemetry-to-builtin"
  resource_group_name = var.resource_group_name
  iothub_name         = azurerm_iothub.iothub.name

  source         = "DeviceMessages"
  condition      = "true"
  endpoint_names = ["events"]
  enabled        = true
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
