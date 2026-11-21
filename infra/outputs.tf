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

output "docs_login" {
  description = "Username and password for the API docs pages (/docs, /redoc, /openapi.json)"
  value       = { username = "ems-docs", password = random_password.docs_password.result }
  sensitive   = true
}
