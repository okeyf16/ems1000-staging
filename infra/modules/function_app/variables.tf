variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "env_prefix" { type = string }
variable "app_name" { type = string }
variable "storage_account_name" { type = string }
variable "storage_primary_access_key" { type = string }
variable "storage_primary_connection_string" { type = string }
variable "app_insights_connection_string" { type = string }
variable "iothub_events_endpoint" { type = string }
variable "iothub_events_path" { type = string }
variable "iothub_owner_key" { type = string }
variable "tags" { type = map(string) }

variable "history_table_name" { type = string }
variable "history_retention_days" { type = number }
