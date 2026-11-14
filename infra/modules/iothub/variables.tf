variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "env_prefix" { type = string }
variable "app_name" { type = string }
variable "log_analytics_workspace_id" { type = string }
variable "tags" { type = map(string) }

variable "archive_storage_connection_string" {
  type      = string
  sensitive = true
}
variable "archive_container_name" { type = string }
