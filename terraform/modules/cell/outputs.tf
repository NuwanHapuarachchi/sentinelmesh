output "observations_table" {
  value = aws_dynamodb_table.observations.name
}

output "scorer_function" {
  value = module.scorer.name
}

output "scorer_mapping_uuid" {
  value = aws_lambda_event_source_mapping.scorer.uuid
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.escalation.arn
}

output "queues" {
  value = { for t, q in aws_sqs_queue.tier : t => q.url }
}
