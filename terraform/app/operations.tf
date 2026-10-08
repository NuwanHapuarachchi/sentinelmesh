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
    HEARTBEAT_ZONES   = join(",", keys(local.heartbeat_zones))
  }

  policy_statements = [
    { Effect = "Allow", Action = "dynamodb:GetItem", Resource = local.a.zones },
    { Effect = "Allow", Action = "dynamodb:DeleteItem", Resource = local.a.zone_state },
    { Effect = "Allow", Action = "dynamodb:Query", Resource = local.a.inbox },
    { Effect = "Allow", Action = "dynamodb:PutItem", Resource = local.a.subscribers },
    {
      Effect   = "Allow"
      Action   = "iot:Publish"
      Resource = [for z in keys(local.heartbeat_zones) : "arn:aws:iot:${var.region}:${var.account_id}:topic/sm/obs/${z}"]
    },
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

# One per heartbeat zone, keyed by zone rather than cell so a failover drill doesn't trip it.
resource "aws_cloudwatch_metric_alarm" "heartbeat" {
  for_each            = local.heartbeat_zones
  alarm_name          = "${var.prefix}-heartbeat-${each.key}-missing"
  alarm_description   = "No test alert has reached the phone through cell ${each.value}'s full path (zone ${each.key}) for 20 minutes."
  namespace           = "SentinelMesh"
  metric_name         = "HeartbeatOK"
  dimensions          = { Zone = each.key }
  statistic           = "Maximum"
  period              = 600
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.officers.arn]
}

locals {
  dlq_meaning = {
    dispatch = "Alerts failed to dispatch; people may be uncovered."
    stream   = "Observations failed scoring after retries; zones may be mis-scored."
    archive  = "Observations failed to reach the evidence lake; post-event replay will have a gap."
  }
  dead_letter_queues = merge([for c, m in module.cell : {
    for kind, queue in m.dead_letter_queues : "${c}-${kind}" => { cell = c, kind = kind, queue = queue }
  }]...)
}

resource "aws_cloudwatch_metric_alarm" "dlq" {
  for_each            = local.dead_letter_queues
  alarm_name          = "${var.prefix}-${each.key}-dlq"
  alarm_description   = "Cell ${each.value.cell}: ${local.dlq_meaning[each.value.kind]}"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = each.value.queue }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.officers.arn]
}

# The dispatch alarms used to be keyed by cell alone; keep them (same names) instead of recreating them.
moved {
  from = aws_cloudwatch_metric_alarm.dlq["a"]
  to   = aws_cloudwatch_metric_alarm.dlq["a-dispatch"]
}

moved {
  from = aws_cloudwatch_metric_alarm.dlq["b"]
  to   = aws_cloudwatch_metric_alarm.dlq["b-dispatch"]
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
        title = "Heartbeat per cell: full path, reading to phone (ms)", region = var.region, stat = "Maximum", period = 600,
      metrics = [for z, c in local.heartbeat_zones : ["SentinelMesh", "HeartbeatE2EMs", "Zone", z, { label = "${z} (cell ${c})" }]] } },
      { type = "metric", x = 8, y = 0, width = 8, height = 6, properties = {
        title = "Time to first alert, p95 (ms)", region = var.region, stat = "p95", period = 300,
      metrics = [["SentinelMesh", "TimeToFirstAlertMs", "Tier", "imminent"], ["...", "warning"], ["...", "advisory"]] } },
      { type = "metric", x = 16, y = 0, width = 8, height = 6, properties = {
        title = "Blind zones per cell", region = var.region, stat = "Maximum", period = 60,
      metrics = [for c in sort(tolist(local.cells)) : ["SentinelMesh", "BlindZones", "Cell", c]] } },
      { type = "metric", x = 0, y = 6, width = 8, height = 6, properties = {
        title = "Scoring latency p99 (ms)", region = var.region, stat = "p99", period = 60,
      metrics = [for c in sort(tolist(local.cells)) : ["SentinelMesh", "ScoreLatencyMs", "Cell", c]] } },
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
    "projection.cell.values"    = join(",", sort(tolist(local.cells)))
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
