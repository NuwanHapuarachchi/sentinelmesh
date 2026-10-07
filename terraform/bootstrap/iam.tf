locals {
  iam_arn         = "arn:aws:iam::${var.account_id}"
  workload_path   = "/${var.prefix}/"
  workload_roles  = "${local.iam_arn}:role${local.workload_path}*"
  workload_policy = "${local.iam_arn}:policy${local.workload_path}*"
}

# The pipeline's identity. Its only permission is to assume the deploy role.
resource "aws_iam_user" "ci" {
  name          = "${var.prefix}-ci"
  force_destroy = true
}

resource "aws_iam_access_key" "ci" {
  user = aws_iam_user.ci.name
}

resource "aws_iam_user_policy" "ci_assume_deploy" {
  name = "assume-deploy-role"
  user = aws_iam_user.ci.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sts:AssumeRole"
      Resource = aws_iam_role.deploy.arn
    }]
  })
}

# Every role the app stack creates must carry this boundary, so workloads can never touch IAM.
resource "aws_iam_policy" "workload_boundary" {
  name        = "${var.prefix}-workload-boundary"
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

# The role every app-stack job assumes. Trust is limited to the CI user; sessions last at most 1 hour.
resource "aws_iam_role" "deploy" {
  name                 = "${var.prefix}-deploy"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = "${local.iam_arn}:root" }
      Action    = "sts:AssumeRole"
      Condition = { ArnEquals = { "aws:PrincipalArn" = aws_iam_user.ci.arn } }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "deploy_power_user" {
  role       = aws_iam_role.deploy.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

# IAM rights limited to roles and policies under the /sentinelmesh/ path, and only with the boundary attached.
resource "aws_iam_role_policy" "deploy_scoped_iam" {
  name = "scoped-iam"
  role = aws_iam_role.deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ChangeWorkloadRolesOnlyWithBoundary"
        Effect = "Allow"
        Action = [
          "iam:CreateRole",
          "iam:PutRolePermissionsBoundary",
          "iam:AttachRolePolicy",
          "iam:DetachRolePolicy",
          "iam:PutRolePolicy",
          "iam:DeleteRolePolicy",
        ]
        Resource  = local.workload_roles
        Condition = { StringEquals = { "iam:PermissionsBoundary" = aws_iam_policy.workload_boundary.arn } }
      },
      {
        Sid    = "ManageWorkloadRoles"
        Effect = "Allow"
        Action = [
          "iam:GetRole",
          "iam:GetRolePolicy",
          "iam:ListRolePolicies",
          "iam:ListAttachedRolePolicies",
          "iam:ListInstanceProfilesForRole",
          "iam:ListRoleTags",
          "iam:TagRole",
          "iam:UntagRole",
          "iam:UpdateRole",
          "iam:UpdateRoleDescription",
          "iam:UpdateAssumeRolePolicy",
          "iam:DeleteRole",
          "iam:PassRole",
        ]
        Resource = local.workload_roles
      },
      {
        Sid    = "ManageWorkloadPolicies"
        Effect = "Allow"
        Action = [
          "iam:CreatePolicy",
          "iam:DeletePolicy",
          "iam:GetPolicy",
          "iam:GetPolicyVersion",
          "iam:ListPolicyVersions",
          "iam:CreatePolicyVersion",
          "iam:DeletePolicyVersion",
          "iam:ListEntitiesForPolicy",
          "iam:TagPolicy",
          "iam:UntagPolicy",
        ]
        Resource = local.workload_policy
      },
      {
        Sid      = "ReadAwsManagedPolicies"
        Effect   = "Allow"
        Action   = ["iam:GetPolicy", "iam:GetPolicyVersion"]
        Resource = "arn:aws:iam::aws:policy/*"
      },
    ]
  })
}
