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

variable "iothub_policy_name" {
  type        = string
  description = "IoT Hub access policy for the API (registry read/write + service connect)"
}

variable "iothub_policy_key" {
  type        = string
  description = "Primary key of that policy (twins, direct methods)"
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

variable "alerts_table_name" {
  type        = string
  description = "Table the API writes alerts to (webhook) and reads them from (GET /api/alerts)"
}

variable "alert_webhook_token" {
  type        = string
  description = "Shared secret Azure Monitor sends in the webhook URL (?token=...)"
  sensitive   = true
}

variable "api_keys" {
  type        = list(string)
  description = "API keys accepted in the X-API-Key header (two, so one can be rotated at a time)"
  sensitive   = true
}

variable "access_table_name" {
  type        = string
  description = "Table where the API counts calls per caller per day"
}

variable "entra_client_id" {
  type        = string
  description = "Client ID of the API's Entra app registration (Microsoft sign-in)"
}

variable "entra_tenant_id" {
  type        = string
  description = "Entra tenant whose accounts may sign in"
}

variable "entra_client_secret" {
  type        = string
  description = "Client secret App Service uses to complete Microsoft sign-in"
  sensitive   = true
}
