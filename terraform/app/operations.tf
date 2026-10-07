# Monitoring that measures reach: heartbeat through the whole path, time-to-first-alert, delivery
# receipts, blind zones. Plus the real forecast feed, cost guardrails and the Athena replay table.

module "heartbeat" {
  source       = "../modules/lambda_fn"
  name         = "${var.prefix}-heartbeat"
  handler      = "heartbeat.handler.handler"
  zip_path     = local.fn_common.zip_path
  zip_hash     = local.fn_common.zip_hash
  boundary_arn = local.fn_common.boundary_arn
  role_path    = local.fn_common.role_path
  timeout      = 120

  environment = {
    ZONES_TABLE       = local.t.zones
    STATE_TABLE       = local.t.zone_state
    INBOX_TABLE       = local.t.inbox
    SUBSCRIBERS_TABLE = local.t.subscribers
    IOT_ENDPOINT      = data.aws_iot_endpoint.data.endpoint_address
  }

  policy_statements = [
    { Effect = "Allow", Action = "dynamodb:GetItem", Resource = local.a.zones },
    { Effect = "Allow", Action = "dynamodb:DeleteItem", Resource = local.a.zone_state },
    { Effect = "Allow", Action = "dynamodb:Query", Resource = local.a.inbox },
    { Effect = "Allow", Action = "dynamodb:PutItem", Resource = local.a.subscribers },
    { Effect = "Allow", Action = "iot:Publish", Resource = "arn:aws:iot:${var.region}:${var.account_id}:topic/sm/obs/H00" },
  ]
}

module "forecast_feed" {
  source       = "../modules/lambda_fn"
  name         = "${var.prefix}-forecast-feed"
  handler      = "forecast_feed.handler.handler"
  zip_path     = local.fn_common.zip_path
  zip_hash     = local.fn_common.zip_hash
  boundary_arn = local.fn_common.boundary_arn
  role_path    = local.fn_common.role_path
  timeout      = 60

  environment = {
    ZONES_TABLE  = local.t.zones
    IOT_ENDPOINT = data.aws_iot_endpoint.data.endpoint_address
  }

  policy_statements = [
    { Effect = "Allow", Action = "dynamodb:Scan", Resource = local.a.zones },
    { Effect = "Allow", Action = "iot:Publish", Resource = "arn:aws:iot:${var.region}:${var.account_id}:topic/sm/obs/*" },
  ]
}

resource "aws_iam_role" "ops_scheduler" {
  name                 = "${var.prefix}-ops-scheduler"
  path                 = local.role_path
  permissions_boundary = local.boundary_arn
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "ops_scheduler" {
  name = "invoke"
  role = aws_iam_role.ops_scheduler.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "lambda:InvokeFunction", Resource = [module.heartbeat.arn, module.forecast_feed.arn] }]
  })
}

resource "aws_scheduler_schedule" "heartbeat" {
  name                = "${var.prefix}-heartbeat"
  schedule_expression = "rate(10 minutes)"
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = module.heartbeat.arn
    role_arn = aws_iam_role.ops_scheduler.arn
  }
}

resource "aws_scheduler_schedule" "forecast" {
  name                = "${var.prefix}-forecast-feed"
  schedule_expression = "rate(1 hour)"
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = module.forecast_feed.arn
    role_arn = aws_iam_role.ops_scheduler.arn
  }
}

# ---------- alarms (to the officers topic) ----------

resource "aws_cloudwatch_metric_alarm" "heartbeat" {
  alarm_name          = "${var.prefix}-heartbeat-missing"
  alarm_description   = "No test alert has reached the phone through the full path for 20 minutes."
  namespace           = "SentinelMesh"
  metric_name         = "HeartbeatOK"
  dimensions          = { Cell = "a" }
  statistic           = "Maximum"
  period              = 600
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.officers.arn]
}

resource "aws_cloudwatch_metric_alarm" "dlq" {
  for_each            = { for c, m in module.cell : c => m }
  alarm_name          = "${var.prefix}-${each.key}-dispatch-dlq"
  alarm_description   = "Alerts failed to dispatch in cell ${each.key}; people may be uncovered."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = "${var.prefix}-${each.key}-dispatch-dlq" }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.officers.arn]
}

resource "aws_cloudwatch_metric_alarm" "ttfa" {
  alarm_name          = "${var.prefix}-time-to-first-alert"
  alarm_description   = "Time from first reading to first confirmed delivery is above 90 s."
  namespace           = "SentinelMesh"
  metric_name         = "TimeToFirstAlertMs"
  dimensions          = { Tier = "imminent" }
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 1
  threshold           = 90000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.officers.arn]
}

# ---------- dashboard ----------

resource "aws_cloudwatch_dashboard" "ops" {
  dashboard_name = "${var.prefix}-operations"
  dashboard_body = jsonencode({
    widgets = [
      { type = "metric", x = 0, y = 0, width = 8, height = 6, properties = {
        title = "Heartbeat: full path, reading to phone (ms)", region = var.region, stat = "Maximum", period = 600,
      metrics = [["SentinelMesh", "HeartbeatE2EMs", "Cell", "a"]] } },
      { type = "metric", x = 8, y = 0, width = 8, height = 6, properties = {
        title = "Time to first alert, p95 (ms)", region = var.region, stat = "p95", period = 300,
      metrics = [["SentinelMesh", "TimeToFirstAlertMs", "Tier", "imminent"], ["...", "warning"], ["...", "advisory"]] } },
      { type = "metric", x = 16, y = 0, width = 8, height = 6, properties = {
        title = "Blind zones per cell", region = var.region, stat = "Maximum", period = 60,
      metrics = [["SentinelMesh", "BlindZones", "Cell", "a"], ["...", "b"]] } },
      { type = "metric", x = 0, y = 6, width = 8, height = 6, properties = {
        title = "Scoring latency p99 (ms)", region = var.region, stat = "p99", period = 60,
      metrics = [["SentinelMesh", "ScoreLatencyMs", "Cell", "a"], ["...", "b"]] } },
      { type = "metric", x = 8, y = 6, width = 8, height = 6, properties = {
        title = "Delivery receipts", region = var.region, stat = "Sum", period = 300,
      metrics = [["SentinelMesh", "DeliveryReceipts", "Channel", "sim", "Tier", "imminent"], ["...", "warning"], ["...", "advisory"]] } },
      { type = "metric", x = 16, y = 6, width = 8, height = 6, properties = {
        title = "Citizen reports", region = var.region, stat = "Sum", period = 300,
      metrics = [["SentinelMesh", "CitizenReports", "Kind", "flooding"], ["...", "water_rising"], ["...", "landslide"], ["...", "crack"]] } },
    ]
  })
}

# ---------- cost guardrails ----------

resource "aws_budgets_budget" "monthly" {
  name         = "${var.prefix}-monthly"
  budget_type  = "COST"
  limit_amount = "30"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = var.officer_email == "" ? [] : [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.officer_email]
    }
  }
}

# ---------- replay: Athena over the evidence lake ----------

resource "aws_glue_catalog_database" "evidence" {
  name = "${var.prefix}_evidence"
}

resource "aws_glue_catalog_table" "observations" {
  name          = "observations"
  database_name = aws_glue_catalog_database.evidence.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "projection.enabled"        = "true"
    "projection.cell.type"      = "enum"
    "projection.cell.values"    = "a,b"
    "projection.dt.type"        = "date"
    "projection.dt.format"      = "yyyy-MM-dd"
    "projection.dt.range"       = "2026-10-01,NOW"
    "projection.hour.type"      = "integer"
    "projection.hour.range"     = "0,23"
    "projection.hour.digits"    = "2"
    "storage.location.template" = "s3://${module.shared.buckets.evidence}/obs/cell=$${cell}/dt=$${dt}/hour=$${hour}/"
  }

  partition_keys {
    name = "cell"
    type = "string"
  }
  partition_keys {
    name = "dt"
    type = "string"
  }
  partition_keys {
    name = "hour"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${module.shared.buckets.evidence}/obs/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
    }

    dynamic "columns" {
      for_each = {
        zone_id        = "string", sk = "string", rx_ms = "bigint", source_class = "string", source_id = "string",
        ts             = "double", drill = "boolean", level_m = "double", rain_24h_mm = "double", tilt_deg = "double",
        fc_rain_24h_mm = "double", report_kind = "string", report_sev = "int", principal = "string",
      }
      content {
        name = columns.key
        type = columns.value
      }
    }
  }
}

resource "aws_athena_workgroup" "replay" {
  name          = "${var.prefix}-replay"
  force_destroy = true

  configuration {
    result_configuration {
      output_location = "s3://${module.shared.buckets.evidence}/athena-results/"
    }
  }
}
