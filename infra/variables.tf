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

variable "offline_grace_minutes" {
  type        = number
  description = "Minutes a device may stay disconnected (Event Grid) before the Device offline alert is raised"
  default     = 2
  validation {
    condition     = var.offline_grace_minutes >= 1 && var.offline_grace_minutes <= 60
    error_message = "Use 1 to 60 minutes."
  }
}

variable "alerts_retention_days" {
  type        = number
  description = "Days to keep resolved alerts in the Alerts table before the daily purge deletes them"
  default     = 30
}

variable "latency_alert_evaluation_periods" {
  type        = number
  description = "How many consecutive 15-minute checks the latency alert looks at (1 to 6)"
  default     = 2
  validation {
    condition     = var.latency_alert_evaluation_periods >= 1 && var.latency_alert_evaluation_periods <= 6
    error_message = "Use 1 to 6."
  }
}

variable "latency_alert_failing_periods" {
  type        = number
  description = "How many of those checks must be slow before the latency alert fires (2 of 2 = two in a row)"
  default     = 2
  validation {
    condition     = var.latency_alert_failing_periods >= 1 && var.latency_alert_failing_periods <= 6
    error_message = "Use 1 to 6 (and no more than latency_alert_evaluation_periods)."
  }
}
