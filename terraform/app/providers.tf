provider "aws" {
  region = var.region

  # Refuse to run against any other AWS account.
  allowed_account_ids = [var.account_id]

  # Applied to every resource, so one tag search can confirm a full teardown.
  default_tags {
    tags = {
      Project   = "SentinelMesh"
      Team      = "TheByteForce"
      ManagedBy = "terraform"
      Stack     = "app"
    }
  }
}
