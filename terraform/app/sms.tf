# AWS End User Messaging SMS (TF_VAR_sms_provider = "eum"): a configuration set whose delivery events go
# through SNS to the sms_events function, which turns them into receipts (services/sms_events/handler.py).
# Nothing here exists unless eum is chosen, so the default stack never touches the service.

locals {
  use_eum = local.sms_provider == "eum"
}

resource "aws_pinpointsmsvoicev2_configuration_set" "alerts" {
  count                = local.use_eum ? 1 : 0
  name                 = "${var.prefix}-alerts"
  default_message_type = "TRANSACTIONAL"
}

resource "aws_sns_topic" "sms_events" {
  count = local.use_eum ? 1 : 0
  name  = "${var.prefix}-sms-events"
}

resource "aws_sns_topic_policy" "sms_events" {
  count = local.use_eum ? 1 : 0
  arn   = aws_sns_topic.sms_events[0].arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sms-voice.amazonaws.com" }
      Action    = "sns:Publish"
      Resource  = aws_sns_topic.sms_events[0].arn
      Condition = { StringEquals = { "aws:SourceAccount" = var.account_id } }
    }]
  })
}

# Only outcomes: delivered to the handset, or a final failure. Progress events (carrier accepted, queued) are
# not receipts.
resource "aws_pinpointsmsvoicev2_event_destination" "receipts" {
  count                  = local.use_eum ? 1 : 0
  configuration_set_name = aws_pinpointsmsvoicev2_configuration_set.alerts[0].name
  event_destination_name = "receipts"
  matching_event_types = [
    "TEXT_DELIVERED", "TEXT_INVALID", "TEXT_INVALID_MESSAGE", "TEXT_UNREACHABLE", "TEXT_CARRIER_UNREACHABLE",
    "TEXT_BLOCKED", "TEXT_CARRIER_BLOCKED", "TEXT_SPAM", "TEXT_TTL_EXPIRED", "TEXT_UNKNOWN", "TEXT_PROTECT_BLOCKED",
  ]

  sns_destination {
    topic_arn = aws_sns_topic.sms_events[0].arn
  }

  depends_on = [aws_sns_topic_policy.sms_events]
}

module "sms_events" {
  source       = "../modules/lambda_fn"
  count        = local.use_eum ? 1 : 0
  name         = "${var.prefix}-sms-events"
  handler      = "sms_events.handler.handler"
  zip_path     = local.fn_common.zip_path
  zip_hash     = local.fn_common.zip_hash
  boundary_arn = local.fn_common.boundary_arn
  role_path    = local.fn_common.role_path

  environment = {
    INBOX_TABLE      = local.t.inbox
    DELIVERIES_TABLE = local.t.deliveries
    ALERTS_TABLE     = local.t.alerts
  }

  policy_statements = [
    { Effect = "Allow", Action = "dynamodb:UpdateItem", Resource = [local.a.inbox, local.a.alerts] },
    { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:UpdateItem"], Resource = local.a.deliveries },
  ]
}

resource "aws_lambda_permission" "sms_events" {
  count         = local.use_eum ? 1 : 0
  statement_id  = "AllowSmsEvents"
  action        = "lambda:InvokeFunction"
  function_name = module.sms_events[0].name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.sms_events[0].arn
}

resource "aws_sns_topic_subscription" "sms_events" {
  count      = local.use_eum ? 1 : 0
  topic_arn  = aws_sns_topic.sms_events[0].arn
  protocol   = "lambda"
  endpoint   = module.sms_events[0].arn
  depends_on = [aws_lambda_permission.sms_events]
}
