output "tables" {
  value = {
    zones      = aws_dynamodb_table.zones.name
    zone_state = aws_dynamodb_table.zone_state.name
  }
}

output "table_arns" {
  value = {
    zones      = aws_dynamodb_table.zones.arn
    zone_state = aws_dynamodb_table.zone_state.arn
  }
}

output "buckets" {
  value = { for k, b in aws_s3_bucket.this : k => b.id }
}

output "bucket_arns" {
  value = { for k, b in aws_s3_bucket.this : k => b.arn }
}
