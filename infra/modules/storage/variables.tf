variable "resource_group_name" {
  type        = string
  description = "Name of the resource group."
}

variable "location" {
  type        = string
  description = "Azure region for the resources."
}

variable "storage_account_name" {
  type        = string
  description = "Globally unique name for the storage account (3-24 lowercase alphanumeric chars)."
}

variable "tags" {
  type        = map(string)
  description = "Tags to apply to the resources."
  default     = {}
}

variable "archive_retention_days" {
  type        = number
  description = "Days to keep raw telemetry archive blobs before lifecycle deletion"
}
