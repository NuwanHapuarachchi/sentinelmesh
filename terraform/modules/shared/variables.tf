variable "prefix" {
  type = string
}

variable "account_id" {
  type = string
}

variable "zones" {
  description = "Zone catalogue from data/zones.json."
  type        = any
}

variable "web_distribution_arn" {
  description = "CloudFront distribution allowed to read the web bucket (empty = none yet)."
  type        = string
  default     = ""
}

variable "deploy_role_arn" {
  description = "The pipeline role; the only principal allowed to delete decision records (for teardown)."
  type        = string
}
