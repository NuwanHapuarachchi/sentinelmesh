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
  description = "Optional email for officer alerts, budget alerts and the Cognito invite. Set via TF_VAR_officer_email."
  type        = string
  default     = ""
}
