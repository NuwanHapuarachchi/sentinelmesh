resource "aws_sqs_queue" "stream_dlq" {
  name                    = "${local.name}-stream-dlq"
  sqs_managed_sse_enabled = true
}

module "scorer" {
  source = "../lambda_fn"

  name         = "${local.name}-scorer"
  handler      = "scorer.handler.handler"
  zip_path     = var.zip_path
  zip_hash     = var.zip_hash
  boundary_arn = var.boundary_arn
  role_path    = var.role_path
  timeout      = 60

  environment = {
    CELL              = var.cell
    ZONES_TABLE       = var.zones_table.name
    STATE_TABLE       = var.state_table.name
    STATE_MACHINE_ARN = aws_sfn_state_machine.escalation.arn
  }

  policy_statements = [
    { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:Scan"], Resource = var.zones_table.arn },
    { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:UpdateItem"], Resource = var.state_table.arn },
    {
      Effect   = "Allow"
      Action   = ["dynamodb:GetRecords", "dynamodb:GetShardIterator", "dynamodb:DescribeStream", "dynamodb:ListStreams"]
      Resource = aws_dynamodb_table.observations.stream_arn
    },
    { Effect = "Allow", Action = "sqs:SendMessage", Resource = aws_sqs_queue.stream_dlq.arn },
    { Effect = "Allow", Action = "states:StartExecution", Resource = aws_sfn_state_machine.escalation.arn },
  ]
}

resource "aws_lambda_event_source_mapping" "scorer" {
  function_name                      = module.scorer.arn
  event_source_arn                   = aws_dynamodb_table.observations.stream_arn
  starting_position                  = "TRIM_HORIZON"
  batch_size                         = 100
  maximum_batching_window_in_seconds = 1
  parallelization_factor             = 2
  bisect_batch_on_function_error     = true
  maximum_retry_attempts             = 3
  function_response_types            = ["ReportBatchItemFailures"]

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.stream_dlq.arn
    }
  }
}

# Watchdog: re-score the cell's zones every minute so silent sensors are noticed.
resource "aws_iam_role" "scheduler" {
  name                 = "${local.name}-scheduler"
  path                 = var.role_path
  permissions_boundary = var.boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "invoke"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "lambda:InvokeFunction", Resource = module.scorer.arn }]
  })
}

resource "aws_scheduler_schedule" "watchdog" {
  name                = "${local.name}-watchdog"
  schedule_expression = "rate(1 minute)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = module.scorer.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ mode = "watchdog" })
  }
}
