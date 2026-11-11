output "storage_account_name" {
  value = azurerm_storage_account.ems_storage.name
}

output "primary_connection_string" {
  value     = azurerm_storage_account.ems_storage.primary_connection_string
  sensitive = true
}

output "primary_access_key" {
  value     = azurerm_storage_account.ems_storage.primary_access_key
  sensitive = true
}
