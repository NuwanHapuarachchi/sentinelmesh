# Shared decide/deliver pieces used by both cells.

resource "aws_sns_topic" "officers" {
  name = "${var.prefix}-officers"
}

resource "aws_sns_topic_subscription" "officer_email" {
  count     = var.officer_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.officers.arn
  protocol  = "email"
  endpoint  = var.officer_email
}

# Third-party SMS credentials. Terraform creates an empty placeholder; the pipeline's
# configure-secrets job writes the real value, so the key never enters Terraform state.
resource "aws_secretsmanager_secret" "sms" {
  name                    = "${var.prefix}/sms-provider"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "sms_placeholder" {
  secret_id     = aws_secretsmanager_secret.sms.id
  secret_string = jsonencode({ api_key = "", sender_id = "", dlr_secret = "" })

  lifecycle {
    ignore_changes = [secret_string]
  }
}

module "approvals" {
  source = "../modules/lambda_fn"

  name         = "${var.prefix}-approvals"
  handler      = "approvals.handler.handler"
  zip_path     = data.archive_file.services.output_path
  zip_hash     = data.archive_file.services.output_base64sha256
  boundary_arn = local.boundary_arn
  role_path    = local.role_path

  environment = {
    APPROVALS_TABLE    = module.shared.tables.approvals
    OFFICERS_TOPIC_ARN = aws_sns_topic.officers.arn
  }

  policy_statements = [
    {
      Effect   = "Allow"
      Action   = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
      Resource = [module.shared.table_arns.approvals, "${module.shared.table_arns.approvals}/index/*"]
    },
    { Effect = "Allow", Action = "sns:Publish", Resource = aws_sns_topic.officers.arn },
    {
      Effect   = "Allow"
      Action   = ["states:SendTaskSuccess", "states:SendTaskFailure"]
      Resource = [for c in module.cell : c.state_machine_arn]
    },
  ]
}

locals {
  delivery = {
    alerts             = { name = module.shared.tables.alerts, arn = module.shared.table_arns.alerts }
    approvals          = { name = module.shared.tables.approvals, arn = module.shared.table_arns.approvals }
    subscribers        = { name = module.shared.tables.subscribers, arn = module.shared.table_arns.subscribers }
    inbox              = { name = module.shared.tables.inbox, arn = module.shared.table_arns.inbox }
    web_bucket         = { name = module.shared.buckets.web, arn = module.shared.bucket_arns.web }
    decisions_bucket   = { name = module.shared.buckets.decisions, arn = module.shared.bucket_arns.decisions }
    approvals_fn_arn   = "arn:aws:lambda:${var.region}:${var.account_id}:function:${var.prefix}-approvals"
    officers_topic_arn = aws_sns_topic.officers.arn
    sms_secret_arn     = aws_secretsmanager_secret.sms.arn
  }

  sms_config = merge(
    { SMS_PROVIDER = var.sms_provider_url == "" ? "simulator" : "http" },
    var.sms_provider_url == "" ? {} : merge({ SMS_HTTP_URL = var.sms_provider_url }, var.sms_http_options),
  )
}
