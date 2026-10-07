terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67"
    }
    gitlab = {
      source  = "gitlabhq/gitlab"
      version = "~> 19.4"
    }
  }

  # GitLab-managed state; the pipeline supplies the address and job token via TF_HTTP_* variables.
  backend "http" {}
}
