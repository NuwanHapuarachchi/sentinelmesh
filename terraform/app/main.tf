data "aws_caller_identity" "current" {}

data "aws_iam_session_context" "pipeline" {
  arn = data.aws_caller_identity.current.arn
}

locals {
  zones = jsondecode(file("${path.module}/data/zones.json"))

  boundary_arn = aws_iam_policy.workload_boundary.arn

  # Principal allowed to delete decision records (teardown): whoever runs Terraform, as an IAM
  # user or role ARN (session ARNs are resolved to their role).
  deploy_role_arn = data.aws_iam_session_context.pipeline.issuer_arn
}

module "shared" {
  source = "../modules/shared"

  prefix          = var.prefix
  account_id      = var.account_id
  zones           = local.zones
  deploy_role_arn = local.deploy_role_arn
}
