variable "location" {
  type        = string
  description = "Azure region (e.g., North Europe)"
  default     = "North Europe"
}

variable "app_name" {
  type        = string
  description = "Base application name (e.g., ems1000)"
  default     = "ems1000"
}

variable "env_prefix" {
  type        = string
  description = "Environment prefix (e.g., stg, prd)"
}

variable "daily_quota_gb" {
  type        = number
  description = "Daily Log Analytics ingestion cap in GB"
  default     = 1
}

variable "tags" {
  type        = map(string)
  description = "Default tags applied to all resources"
  default = {
    Project = "EMS1000"
  }
}

variable "history_retention_days" {
  type        = number
  default     = 9
  description = "Days of per-message history kept in the DeviceTelemetry table"
}

variable "archive_retention_days" {
  type        = number
  default     = 30
  description = "Days raw telemetry archive blobs are kept before lifecycle deletion"
}

variable "missing_telemetry_silence_minutes" {
  type        = number
  description = "Minutes without telemetry before the missing-telemetry alert fires for a device"
  default     = 5
}

variable "missing_telemetry_check_minutes" {
  type        = number
  description = "How often the missing-telemetry alert rule runs (1, 5, 10 or 15 minutes)"
  default     = 5
}
