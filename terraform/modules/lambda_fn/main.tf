# One Lambda function with its own least-privilege role, boundary, log group and X-Ray tracing.
# All functions share one code zip (services/) and differ only in handler and permissions.

variable "name" {
  type = string
}

variable "handler" {
  description = "e.g. scorer.handler.handler"
  type        = string
}

variable "zip_path" {
  type = string
}

variable "zip_hash" {
  type = string
}

variable "boundary_arn" {
  type = string
}

variable "role_path" {
  type = string
}

variable "policy_statements" {
  description = "IAM statements for this function only."
  type        = any
  default     = []
}

variable "environment" {
  type    = map(string)
  default = {}
}

variable "timeout" {
  type    = number
  default = 30
}

variable "memory" {
  type    = number
  default = 256
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# The function writes only to this group; it has no logs:CreateLogGroup, so nothing can
# recreate the group after destroy.
resource "aws_cloudwatch_log_group" "this" {
  name = "/aws/lambda/${var.name}"
}

resource "aws_iam_role" "this" {
  name                 = var.name
  path                 = var.role_path
  permissions_boundary = var.boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "this" {
  name = "function"
  role = aws_iam_role.this.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Effect   = "Allow"
          Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
          Resource = "${aws_cloudwatch_log_group.this.arn}:*"
        },
        {
          Effect   = "Allow"
          Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
          Resource = "*"
        },
      ],
      var.policy_statements,
    )
  })
}

resource "aws_lambda_function" "this" {
  function_name    = var.name
  role             = aws_iam_role.this.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = var.handler
  filename         = var.zip_path
  source_code_hash = var.zip_hash
  timeout          = var.timeout
  memory_size      = var.memory

  environment {
    variables = merge({ PYTHONPATH = "/var/task" }, var.environment)
  }

  tracing_config {
    mode = "Active"
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.this.name
  }

  depends_on = [aws_iam_role_policy.this]
}

output "arn" {
  value = aws_lambda_function.this.arn
}

output "name" {
  value = aws_lambda_function.this.function_name
}

output "role_arn" {
  value = aws_iam_role.this.arn
}

output "role_name" {
  value = aws_iam_role.this.name
}
