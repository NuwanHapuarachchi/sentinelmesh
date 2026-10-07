provider "aws" {
  region = var.region

  # Refuse to run against any other AWS account.
  allowed_account_ids = [var.account_id]

  default_tags {
    tags = {
      Project   = "SentinelMesh"
      Team      = "TheByteForce"
      ManagedBy = "terraform"
      Stack     = "bootstrap"
    }
  }
}

# Reads GITLAB_TOKEN from the environment (a masked, protected CI/CD variable).
provider "gitlab" {}
