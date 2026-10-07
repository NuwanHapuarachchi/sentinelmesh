# Second stream consumer: every observation goes to the evidence lake for post-event replay in Athena.

module "archiver" {
  source = "../lambda_fn"

  name         = "${local.name}-archiver"
  handler      = "archiver.handler.handler"
  zip_path     = var.zip_path
  zip_hash     = var.zip_hash
  boundary_arn = var.boundary_arn
  role_path    = var.role_path
  timeout      = 60

  environment = {
    CELL            = var.cell
    EVIDENCE_BUCKET = var.evidence_bucket.name
  }

  policy_statements = [
    {
      Effect   = "Allow"
      Action   = ["dynamodb:GetRecords", "dynamodb:GetShardIterator", "dynamodb:DescribeStream", "dynamodb:ListStreams"]
      Resource = aws_dynamodb_table.observations.stream_arn
    },
    { Effect = "Allow", Action = "s3:PutObject", Resource = "${var.evidence_bucket.arn}/obs/cell=${var.cell}/*" },
  ]
}

resource "aws_lambda_event_source_mapping" "archiver" {
  function_name                      = module.archiver.arn
  event_source_arn                   = aws_dynamodb_table.observations.stream_arn
  starting_position                  = "TRIM_HORIZON"
  batch_size                         = 1000
  maximum_batching_window_in_seconds = 60
  maximum_retry_attempts             = 5
  bisect_batch_on_function_error     = true
}
