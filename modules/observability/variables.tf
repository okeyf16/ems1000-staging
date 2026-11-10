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
