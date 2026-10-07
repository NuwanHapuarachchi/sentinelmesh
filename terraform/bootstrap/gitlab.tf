# The app-stack jobs read these. Names avoid AWS_ACCESS_KEY_ID so the SDKs never pick the
# CI user's key directly; jobs always go through the deploy role.
resource "gitlab_project_variable" "access_key_id" {
  project     = var.gitlab_project
  key         = "SM_AWS_ACCESS_KEY_ID"
  value       = aws_iam_access_key.ci.id
  description = "sentinelmesh-ci access key id (managed by terraform/bootstrap)"
  protected   = true
  masked      = true
  raw         = true
}

resource "gitlab_project_variable" "secret_access_key" {
  project     = var.gitlab_project
  key         = "SM_AWS_SECRET_ACCESS_KEY"
  value       = aws_iam_access_key.ci.secret
  description = "sentinelmesh-ci secret key (managed by terraform/bootstrap)"
  protected   = true
  masked      = true
  hidden      = true
  raw         = true
}

resource "gitlab_project_variable" "deploy_role_arn" {
  project     = var.gitlab_project
  key         = "SM_AWS_DEPLOY_ROLE_ARN"
  value       = aws_iam_role.deploy.arn
  description = "Role the app-stack jobs assume (managed by terraform/bootstrap)"
  protected   = true
  raw         = true
}
