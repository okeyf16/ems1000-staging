resource "azurerm_storage_account" "ems_storage" {
  name                     = var.storage_account_name
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  tags                     = var.tags
}

# Container for historical telemetry archive (Append Blobs)
resource "azurerm_storage_container" "telemetry_archive" {
  name                  = "telemetry-archive"
  storage_account_name  = azurerm_storage_account.ems_storage.name
  container_access_type = "private"
}

# Container for payloads that fail JSON parsing (Dead-letter)[cite: 1]
resource "azurerm_storage_container" "dead_letter" {
  name                  = "dead-letter"
  storage_account_name  = azurerm_storage_account.ems_storage.name
  container_access_type = "private"
}

# Table for the FastAPI backend to read the latest device state[cite: 1, 4]
resource "azurerm_storage_table" "device_state" {
  name                 = "DeviceState"
  storage_account_name = azurerm_storage_account.ems_storage.name
}
