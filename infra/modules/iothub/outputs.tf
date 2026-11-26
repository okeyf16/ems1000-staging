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

# Least-privilege keys: the API and the Function each get their own policy instead of the
# full-rights iothubowner (which stays for admins in the portal only). See main.tf.
output "api_policy_name" {
  value = azurerm_iothub_shared_access_policy.api.name
}

output "api_policy_primary_key" {
  value     = azurerm_iothub_shared_access_policy.api.primary_key
  sensitive = true
}

output "function_policy_name" {
  value = azurerm_iothub_shared_access_policy.function.name
}

output "function_policy_primary_key" {
  value     = azurerm_iothub_shared_access_policy.function.primary_key
  sensitive = true
}

output "iothub_name" {
  value = azurerm_iothub.iothub.name
}
