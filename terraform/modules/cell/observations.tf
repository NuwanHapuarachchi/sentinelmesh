locals {
  name = "${var.prefix}-${var.cell}"
  # IoT rule names allow only letters, digits and underscores.
  rule_prefix = replace(local.name, "-", "_")
  # Region and account, read from an ARN the module is given (arn:aws:dynamodb:<region>:<account>:table/...).
  region     = split(":", var.zones_table.arn)[3]
  account_id = split(":", var.zones_table.arn)[4]
}

# The cell's observation bus. Production uses Kinesis; this account blocks it, so the prototype
# uses a DynamoDB table + stream: ordered per zone, replayable for 24 h, two consumers.
resource "aws_dynamodb_table" "observations" {
  name                        = "${local.name}-observations"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "zone_id"
  range_key                   = "sk"
  stream_enabled              = true
  stream_view_type            = "NEW_IMAGE"
  deletion_protection_enabled = false

  attribute {
    name = "zone_id"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = false
  }
}

resource "aws_cloudwatch_log_group" "iot_errors" {
  name              = "/${var.prefix}/${var.cell}/iot-rule-errors"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "iot_rules" {
  name                 = "${local.name}-iot-rules"
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

resource "aws_iam_role_policy" "iot_rules" {
  name = "rules"
  role = aws_iam_role.iot_rules.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "dynamodb:GetItem", Resource = var.zones_table.arn },
      { Effect = "Allow", Action = "dynamodb:PutItem", Resource = aws_dynamodb_table.observations.arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.iot_errors.arn}:*" },
    ]
  })
}

# Route every observation for a zone this cell owns into the cell's table. Failover is a data
# change: flip owner_cell in the zones table and the next reading lands in the other cell.
# Fields are listed explicitly so a payload can't inject keys such as zone_id or expires_at.
resource "aws_iot_topic_rule" "route" {
  name        = "${local.rule_prefix}_route"
  enabled     = true
  sql_version = "2016-03-23"
  sql         = <<-SQL
    SELECT topic(3) AS zone_id,
           concat(timestamp(), '#', source_class, '#', newuuid()) AS sk,
           timestamp() AS rx_ms, timestamp() / 1000 + 604800 AS expires_at,
           principal() AS principal, clientid() AS client_id,
           source_class, source_id, ts, drill, qc, level_m, rain_24h_mm, tilt_deg,
           fc_rain_24h_mm, report_kind, report_sev, reporter_hash, hb_id, scenario_id
    FROM 'sm/obs/+'
    WHERE get_dynamodb('${var.zones_table.name}', 'zone_id', topic(3), '${aws_iam_role.iot_rules.arn}').owner_cell = '${var.cell}'
  SQL

  dynamodbv2 {
    role_arn = aws_iam_role.iot_rules.arn
    put_item {
      table_name = aws_dynamodb_table.observations.name
    }
  }

  error_action {
    cloudwatch_logs {
      log_group_name = aws_cloudwatch_log_group.iot_errors.name
      role_arn       = aws_iam_role.iot_rules.arn
    }
  }

  depends_on = [aws_iam_role_policy.iot_rules]
}

# Field devices publish to sm/dev/{zone}/{class} with their own certificates (terraform/app/iot.tf). The
# class comes from the topic (which the device policy pins to the thing's attributes) and the source from the
# certificate's client id, so a device can't claim to be a forecast, another station or a drill.
resource "aws_iot_topic_rule" "route_devices" {
  name        = "${local.rule_prefix}_route_devices"
  enabled     = true
  sql_version = "2016-03-23"
  sql         = <<-SQL
    SELECT topic(3) AS zone_id, topic(4) AS source_class,
           concat(timestamp(), '#', topic(4), '#', newuuid()) AS sk,
           timestamp() AS rx_ms, timestamp() / 1000 + 604800 AS expires_at,
           principal() AS principal, clientid() AS client_id, clientid() AS source_id,
           ts, level_m, rain_24h_mm, tilt_deg
    FROM 'sm/dev/+/+'
    WHERE get_dynamodb('${var.zones_table.name}', 'zone_id', topic(3), '${aws_iam_role.iot_rules.arn}').owner_cell = '${var.cell}'
  SQL

  dynamodbv2 {
    role_arn = aws_iam_role.iot_rules.arn
    put_item {
      table_name = aws_dynamodb_table.observations.name
    }
  }

  error_action {
    cloudwatch_logs {
      log_group_name = aws_cloudwatch_log_group.iot_errors.name
      role_arn       = aws_iam_role.iot_rules.arn
    }
  }

  depends_on = [aws_iam_role_policy.iot_rules]
}
