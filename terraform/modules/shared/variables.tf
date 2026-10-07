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

variable "deploy_role_arn" {
  description = "The pipeline role; the only principal allowed to delete decision records (for teardown)."
  type        = string
}
