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
