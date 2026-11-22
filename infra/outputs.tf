output "resource_group_name" {
  value = azurerm_resource_group.rg.name
}

output "storage_account_name" {
  value = module.storage.storage_account_name
}

output "app_insights_name" {
  value = module.observability.app_insights_id
}
output "api_base_url" {
  value = module.app_service.api_base_url
}

output "api_keys" {
  description = "API keys for the X-API-Key header (primary and secondary)"
  value       = { for name, key in random_password.api_key : name => key.result }
  sensitive   = true
}

output "api_entra_client_id" {
  description = "Client ID of the API's Entra app registration (Microsoft sign-in)"
  value       = azuread_application.api.client_id
}
