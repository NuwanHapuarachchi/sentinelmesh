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

variable "gitlab_project" {
  description = "GitLab project (id or path) whose CI/CD variables receive the pipeline credentials."
  type        = string
  default     = "87349041"
}

variable "prefix" {
  description = "Name prefix for every resource."
  type        = string
  default     = "sentinelmesh"
}
