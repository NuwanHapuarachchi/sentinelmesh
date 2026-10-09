# Android app: anonymous, listen-only. A Cognito guest identity (no sign-up, no phone number) gets
# short-lived credentials that can only connect as itself and subscribe to sm/broadcast/<zone>, where
# dispatch pushes each alert. Phones can't publish anything, so a phone can never fake an alert.

resource "aws_cognito_identity_pool" "app" {
  identity_pool_name               = "${var.prefix}-app-guests"
  allow_unauthenticated_identities = true
  allow_classic_flow               = false
}

resource "aws_iam_role" "app_guest" {
  name                 = "${var.prefix}-app-guest"
  path                 = local.role_path
  permissions_boundary = local.boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = "cognito-identity.amazonaws.com" }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals             = { "cognito-identity.amazonaws.com:aud" = aws_cognito_identity_pool.app.id }
        "ForAnyValue:StringLike" = { "cognito-identity.amazonaws.com:amr" = "unauthenticated" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "app_guest" {
  name = "listen-only"
  role = aws_iam_role.app_guest.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "iot:Connect"
        Resource = "arn:aws:iot:${var.region}:${var.account_id}:client/$${cognito-identity.amazonaws.com:sub}"
      },
      {
        Effect   = "Allow"
        Action   = "iot:Subscribe"
        Resource = "arn:aws:iot:${var.region}:${var.account_id}:topicfilter/sm/broadcast/*"
      },
      {
        Effect   = "Allow"
        Action   = "iot:Receive"
        Resource = "arn:aws:iot:${var.region}:${var.account_id}:topic/sm/broadcast/*"
      },
    ]
  })
}

resource "aws_cognito_identity_pool_roles_attachment" "app" {
  identity_pool_id = aws_cognito_identity_pool.app.id
  roles            = { unauthenticated = aws_iam_role.app_guest.arn }
}
