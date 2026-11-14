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

output "telemetry_table_name" {
  value = azurerm_storage_table.device_telemetry.name
}

output "telemetry_archive_container_name" {
  value = azurerm_storage_container.telemetry_archive.name
}
