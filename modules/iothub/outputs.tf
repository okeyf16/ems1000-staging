output "iothub_id" {
  value = azurerm_iothub.iothub.id
}

# The Function App requires the built-in Event Hub-compatible endpoint details
output "eventhub_compatible_endpoint" {
  value = azurerm_iothub.iothub.event_hub_events_endpoint
}

output "eventhub_compatible_path" {
  value = azurerm_iothub.iothub.event_hub_events_path
}

data "azurerm_iothub_shared_access_policy" "iothubowner" {
  name                = "iothubowner"
  resource_group_name = var.resource_group_name
  iothub_name         = azurerm_iothub.iothub.name
}

output "iothub_owner_primary_key" {
  value     = data.azurerm_iothub_shared_access_policy.iothubowner.primary_key
  sensitive = true
}
