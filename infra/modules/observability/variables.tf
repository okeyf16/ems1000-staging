variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "env_prefix" {
  type        = string
  description = "Prefix for the environment (e.g., stg, prd)."
}

variable "daily_quota_gb" {
  type        = number
  description = "Hard daily ingestion cap to prevent billing spikes."
  default     = 1 # 1 GB/day is highly protective for the pilot[cite: 1]
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "action_group_id" {
  type        = string
  description = "Action group both alert rules notify (webhook to the API's alerts endpoint)"
}

variable "missing_telemetry_silence_minutes" {
  type        = number
  description = "Minutes without telemetry before a device counts as missing (keep above 3x the heartbeat interval)"
  default     = 5
}

variable "missing_telemetry_check_minutes" {
  type        = number
  description = "How often the missing-telemetry rule runs, in minutes (1, 5, 10 or 15; 1 costs several times more)"
  default     = 5
  validation {
    condition     = contains([1, 5, 10, 15], var.missing_telemetry_check_minutes)
    error_message = "Use 1, 5, 10 or 15 minutes."
  }
}
