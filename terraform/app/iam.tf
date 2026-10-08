# Every workload role carries this boundary, so no Lambda, rule or state machine can touch IAM,
# Organizations or account settings, whatever its own policy says. The deploy role can create and delete
# it but is denied new versions (terraform/bootstrap/iam.tf), so changing this policy needs an admin run.
resource "aws_iam_policy" "workload_boundary" {
  name        = "${var.prefix}-workload-boundary"
  path        = "/${var.prefix}/"
  description = "Permissions boundary for SentinelMesh workload roles"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AllowWorkloadServices"
        Effect   = "Allow"
        Action   = "*"
        Resource = "*"
      },
      {
        Sid      = "DenyIdentityAndAccountChanges"
        Effect   = "Deny"
        Action   = ["iam:*", "organizations:*", "account:*"]
        Resource = "*"
      },
    ]
  })
}
