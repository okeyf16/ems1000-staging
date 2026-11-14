variable "resource_group_name" {
  type        = string
  description = "Name of the target resource group."
}

variable "location" {
  type        = string
  description = "Azure region (e.g., North Europe)."
}

variable "env_prefix" {
  type        = string
  description = "Environment prefix (e.g., stg, prd)."
}

variable "app_name" {
  type        = string
  description = "Base application name (e.g., ems1000)."
}

variable "name_suffix" {
  type        = string
  description = "Random suffix to guarantee a globally unique azurewebsites.net hostname."
}

variable "app_service_sku" {
  type        = string
  description = "App Service Plan SKU (F1 for free pilot, B1 when CPU trigger fires)."
  default     = "F1"
}

variable "storage_primary_connection_string" {
  type        = string
  description = "Storage connection string for reading DeviceState Table and Blob history."
  sensitive   = true
}

variable "app_insights_connection_string" {
  type        = string
  description = "Application Insights connection string for OpenTelemetry."
  sensitive   = true
}

variable "iothub_owner_key" {
  type        = string
  description = "IoT Hub iothubowner primary key for invoking start/stop Direct Methods."
  sensitive   = true
}

variable "log_analytics_workspace_id" {
  type        = string
  description = "Log Analytics workspace ID for App Service platform diagnostics."
}

variable "tags" {
  type        = map(string)
  description = "Resource tags."
  default     = {}
}

variable "history_table_name" {
  type        = string
  description = "Table holding per-message telemetry history"
}
