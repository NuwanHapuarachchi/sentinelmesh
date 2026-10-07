variable "cell" {
  description = "Cell id (a, b)."
  type        = string
}

variable "prefix" {
  type = string
}

variable "role_path" {
  type = string
}

variable "boundary_arn" {
  type = string
}

variable "zip_path" {
  type = string
}

variable "zip_hash" {
  type = string
}

variable "zones_table" {
  type = object({ name = string, arn = string })
}

variable "state_table" {
  type = object({ name = string, arn = string })
}

variable "delivery" {
  description = "Shared delivery resources."
  type = object({
    alerts             = object({ name = string, arn = string })
    approvals          = object({ name = string, arn = string })
    subscribers        = object({ name = string, arn = string })
    inbox              = object({ name = string, arn = string })
    web_bucket         = object({ name = string, arn = string })
    decisions_bucket   = object({ name = string, arn = string })
    approvals_fn_arn   = string
    officers_topic_arn = string
    sms_secret_arn     = string
  })
}

variable "sms_config" {
  description = "SMS_PROVIDER and SMS_HTTP_* settings for dispatch (no secrets)."
  type        = map(string)
  default     = { SMS_PROVIDER = "simulator" }
}

variable "rules_version" {
  description = "Recorded in every decision record (the git commit)."
  type        = string
  default     = "dev"
}
