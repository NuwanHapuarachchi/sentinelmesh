locals {
  # Separate queues and concurrency per tier, so an imminent alert never waits behind advisories:
  # plan = planners (dispatch) per tier, send = senders per tier, sized to the SMS provider's throughput.
  tiers = {
    imminent = { plan = 5, send = 20 }
    warning  = { plan = 2, send = 5 }
    advisory = { plan = 2, send = 2 }
  }
  sender_timeout = 120
}

resource "aws_sqs_queue" "dispatch_dlq" {
  name                    = "${local.name}-dispatch-dlq"
  sqs_managed_sse_enabled = true
}

resource "aws_sqs_queue" "tier" {
  for_each                   = local.tiers
  name                       = "${local.name}-${each.key}"
  sqs_managed_sse_enabled    = true
  visibility_timeout_seconds = 120
  redrive_policy             = jsonencode({ deadLetterTargetArn = aws_sqs_queue.dispatch_dlq.arn, maxReceiveCount = 3 })
}

# Batches of recipients planned by dispatch. A batch whose sends failed transiently comes back after the
# visibility timeout (6 x the sender timeout, as Lambda recommends) and is retried for those recipients only;
# after three tries it lands in the dead-letter queue, which is alarmed.
resource "aws_sqs_queue" "send_dlq" {
  name                    = "${local.name}-send-dlq"
  sqs_managed_sse_enabled = true
}

resource "aws_sqs_queue" "send" {
  for_each                   = local.tiers
  name                       = "${local.name}-send-${each.key}"
  sqs_managed_sse_enabled    = true
  visibility_timeout_seconds = 6 * local.sender_timeout
  redrive_policy             = jsonencode({ deadLetterTargetArn = aws_sqs_queue.send_dlq.arn, maxReceiveCount = 3 })
}

module "issuer" {
  source = "../lambda_fn"

  name         = "${local.name}-issuer"
  handler      = "issuer.handler.handler"
  zip_path     = var.zip_path
  zip_hash     = var.zip_hash
  boundary_arn = var.boundary_arn
  role_path    = var.role_path

  environment = {
    CELL             = var.cell
    ALERTS_TABLE     = var.delivery.alerts.name
    APPROVALS_TABLE  = var.delivery.approvals.name
    ZONES_TABLE      = var.zones_table.name
    WEB_BUCKET       = var.delivery.web_bucket.name
    DECISIONS_BUCKET = var.delivery.decisions_bucket.name
    QUEUE_IMMINENT   = aws_sqs_queue.tier["imminent"].url
    QUEUE_WARNING    = aws_sqs_queue.tier["warning"].url
    QUEUE_ADVISORY   = aws_sqs_queue.tier["advisory"].url
    RULES_VERSION    = var.rules_version
  }

  policy_statements = [
    { Effect = "Allow", Action = "dynamodb:GetItem", Resource = var.zones_table.arn },
    { Effect = "Allow", Action = "dynamodb:PutItem", Resource = var.delivery.alerts.arn },
    { Effect = "Allow", Action = "dynamodb:UpdateItem", Resource = var.delivery.approvals.arn },
    { Effect = "Allow", Action = "s3:PutObject", Resource = "${var.delivery.web_bucket.arn}/cap/*" },
    { Effect = "Allow", Action = "s3:PutObject", Resource = "${var.delivery.decisions_bucket.arn}/decisions/*" },
  ]
}

# Planner: cell broadcast first, then the zone's subscribers in batches onto the tier's send queue.
module "dispatch" {
  source = "../lambda_fn"

  name         = "${local.name}-dispatch"
  handler      = "dispatch.handler.handler"
  zip_path     = var.zip_path
  zip_hash     = var.zip_hash
  boundary_arn = var.boundary_arn
  role_path    = var.role_path
  timeout      = 90

  environment = {
    CELL                = var.cell
    SUBSCRIBERS_TABLE   = var.delivery.subscribers.name
    ALERTS_TABLE        = var.delivery.alerts.name
    WEB_BUCKET          = var.delivery.web_bucket.name
    SEND_QUEUE_IMMINENT = aws_sqs_queue.send["imminent"].url
    SEND_QUEUE_WARNING  = aws_sqs_queue.send["warning"].url
    SEND_QUEUE_ADVISORY = aws_sqs_queue.send["advisory"].url
  }

  policy_statements = [
    { Effect = "Allow", Action = "dynamodb:Query", Resource = "${var.delivery.subscribers.arn}/index/by-zone" },
    { Effect = "Allow", Action = "dynamodb:UpdateItem", Resource = var.delivery.alerts.arn },
    { Effect = "Allow", Action = "s3:PutObject", Resource = "${var.delivery.web_bucket.arn}/cb/*" },
    { Effect = "Allow", Action = "sqs:SendMessage", Resource = [for q in aws_sqs_queue.send : q.arn] },
    {
      Effect   = "Allow"
      Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
      Resource = [for q in aws_sqs_queue.tier : q.arn]
    },
  ]
}

resource "aws_lambda_event_source_mapping" "dispatch" {
  for_each                = local.tiers
  function_name           = module.dispatch.arn
  event_source_arn        = aws_sqs_queue.tier[each.key].arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = each.value.plan
  }
}

# Sender: one batch per invocation, every recipient claimed in the delivery ledger before sending.
module "sender" {
  source = "../lambda_fn"

  name         = "${local.name}-sender"
  handler      = "sender.handler.handler"
  zip_path     = var.zip_path
  zip_hash     = var.zip_hash
  boundary_arn = var.boundary_arn
  role_path    = var.role_path
  timeout      = local.sender_timeout

  environment = merge({
    CELL             = var.cell
    DELIVERIES_TABLE = var.delivery.deliveries.name
    INBOX_TABLE      = var.delivery.inbox.name
    ALERTS_TABLE     = var.delivery.alerts.name
    SMS_SECRET_ARN   = var.delivery.sms_secret_arn
  }, var.sms_config)

  policy_statements = [
    { Effect = "Allow", Action = ["dynamodb:PutItem", "dynamodb:UpdateItem"], Resource = var.delivery.deliveries.arn },
    { Effect = "Allow", Action = "dynamodb:PutItem", Resource = var.delivery.inbox.arn },
    { Effect = "Allow", Action = "dynamodb:UpdateItem", Resource = var.delivery.alerts.arn },
    { Effect = "Allow", Action = "secretsmanager:GetSecretValue", Resource = var.delivery.sms_secret_arn },
    {
      Effect   = "Allow"
      Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
      Resource = [for q in aws_sqs_queue.send : q.arn]
    },
  ]
}

resource "aws_lambda_event_source_mapping" "sender" {
  for_each                = local.tiers
  function_name           = module.sender.arn
  event_source_arn        = aws_sqs_queue.send[each.key].arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = each.value.send
  }
}

# ---------- escalation workflow ----------

resource "aws_cloudwatch_log_group" "escalation" {
  name              = "/aws/vendedlogs/states/${local.name}-escalation"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "escalation" {
  name                 = "${local.name}-escalation"
  path                 = var.role_path
  permissions_boundary = var.boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "escalation" {
  name = "workflow"
  role = aws_iam_role.escalation.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "dynamodb:UpdateItem", Resource = var.state_table.arn },
      { Effect = "Allow", Action = "lambda:InvokeFunction", Resource = [module.issuer.arn, var.delivery.approvals_fn_arn] },
      { Effect = "Allow", Action = "sqs:SendMessage", Resource = [for q in aws_sqs_queue.tier : q.arn] },
      { Effect = "Allow", Action = "sns:Publish", Resource = var.delivery.officers_topic_arn },
      { Effect = "Allow", Action = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"], Resource = "*" },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_sfn_state_machine" "escalation" {
  name     = "${local.name}-escalation"
  role_arn = aws_iam_role.escalation.arn
  type     = "STANDARD"
  definition = templatefile("${path.module}/escalation.asl.json", {
    cell               = var.cell
    zone_state_table   = var.state_table.name
    approvals_fn_arn   = var.delivery.approvals_fn_arn
    issuer_fn_arn      = module.issuer.arn
    officers_topic_arn = var.delivery.officers_topic_arn
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.escalation.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  tracing_configuration {
    enabled = true
  }

  depends_on = [aws_iam_role_policy.escalation]
}

# ---------- hard-threshold bypass ----------
# If the scorer is down, a gauge reading at or above the zone's danger level still starts the
# workflow straight from IoT Core. The workflow's gate removes duplicates.

resource "aws_iam_role" "bypass" {
  name                 = "${local.name}-bypass"
  path                 = var.role_path
  permissions_boundary = var.boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "iot.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "bypass" {
  name = "bypass"
  role = aws_iam_role.bypass.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "dynamodb:GetItem", Resource = var.zones_table.arn },
      { Effect = "Allow", Action = "states:StartExecution", Resource = aws_sfn_state_machine.escalation.arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.iot_errors.arn}:*" },
    ]
  })
}

resource "aws_iot_topic_rule" "bypass" {
  name        = "${local.rule_prefix}_bypass"
  enabled     = true
  sql_version = "2016-03-23"
  sql         = <<-SQL
    SET @z = get_dynamodb('${var.zones_table.name}', 'zone_id', topic(3), '${aws_iam_role.bypass.arn}')
    SELECT topic(3) AS zone_id, 'imminent' AS tier, 'flood' AS hazard, 'hard_threshold' AS reason,
           'bypass' AS origin, '${var.cell}' AS cell, 1.0 AS score, level_m, source_id, drill,
           timestamp() AS trigger_rx_ms
    FROM 'sm/obs/+'
    WHERE source_class = 'gauge' AND level_m < 60 AND @z.owner_cell = '${var.cell}' AND level_m >= @z.danger_level_m
  SQL

  step_functions {
    state_machine_name    = aws_sfn_state_machine.escalation.name
    execution_name_prefix = "bypass"
    role_arn              = aws_iam_role.bypass.arn
  }

  error_action {
    cloudwatch_logs {
      log_group_name = aws_cloudwatch_log_group.iot_errors.name
      role_arn       = aws_iam_role.bypass.arn
    }
  }

  depends_on = [aws_iam_role_policy.bypass]
}
