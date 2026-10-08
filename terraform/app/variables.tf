variable "region" {
  description = "AWS region. The project account only allows ap-southeast-2."
  type        = string
  default     = "ap-southeast-2"
}

variable "account_id" {
  description = "The only AWS account this configuration may touch."
  type        = string
  default     = "759576782403"
}

variable "prefix" {
  description = "Name prefix for every resource."
  type        = string
  default     = "sentinelmesh"
}

variable "officer_email" {
  description = "Optional email for officer alert emails and budget alerts. Set via TF_VAR_officer_email."
  type        = string
  default     = ""
}

variable "sms_provider_url" {
  description = "Third-party SMS gateway send URL. Empty = simulator. Set via TF_VAR_sms_provider_url."
  type        = string
  default     = ""
}

variable "sms_http_options" {
  description = "SMS_HTTP_* settings for the gateway (field names, auth style); see services/common/sms.py."
  type        = map(string)
  default     = {}
}

variable "rules_version" {
  description = "Git commit recorded in every decision record. Set by the pipeline."
  type        = string
  default     = "dev"
}

variable "officers" {
  description = "Console users: email => group, or \"ems:District\" for an EMS user scoped to one district. Groups: duty_officer, met, ems, ngo, viewer. Set via TF_VAR_officers in GitLab."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for spec in values(var.officers) : contains(["duty_officer", "met", "ems", "ngo", "viewer"], split(":", spec)[0])])
    error_message = "Each officer is \"group\" or \"ems:District\", with group one of duty_officer, met, ems, ngo, viewer."
  }

  validation {
    condition     = alltrue([for spec in values(var.officers) : !startswith(spec, "ems") || can(regex("^ems:[^:]+$", spec))])
    error_message = "An ems officer needs a district, e.g. \"ems:Ratnapura\"."
  }
}
