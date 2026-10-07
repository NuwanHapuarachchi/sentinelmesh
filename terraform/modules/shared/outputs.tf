locals {
  all_tables = {
    zones       = aws_dynamodb_table.zones
    zone_state  = aws_dynamodb_table.zone_state
    alerts      = aws_dynamodb_table.alerts
    approvals   = aws_dynamodb_table.approvals
    subscribers = aws_dynamodb_table.subscribers
    inbox       = aws_dynamodb_table.inbox
    ratelimits  = aws_dynamodb_table.ratelimits
  }
}

output "tables" {
  value = { for k, t in local.all_tables : k => t.name }
}

output "table_arns" {
  value = { for k, t in local.all_tables : k => t.arn }
}

output "buckets" {
  value = { for k, b in aws_s3_bucket.this : k => b.id }
}

output "bucket_arns" {
  value = { for k, b in aws_s3_bucket.this : k => b.arn }
}
