output "deploy_role_arn" {
  value = aws_iam_role.deploy.arn
}

output "ci_user_arn" {
  value = aws_iam_user.ci.arn
}

output "workload_boundary_arn" {
  value = local.boundary_arn
}
