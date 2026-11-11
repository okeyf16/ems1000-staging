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
