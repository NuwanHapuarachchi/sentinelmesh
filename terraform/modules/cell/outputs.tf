output "observations_table" {
  value = aws_dynamodb_table.observations.name
}

output "scorer_function" {
  value = module.scorer.name
}

output "scorer_function_arn" {
  value = module.scorer.arn
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

output "dead_letter_queues" {
  description = "Queue names by kind; anything in them means work the cell could not finish."
  value = {
    dispatch = aws_sqs_queue.dispatch_dlq.name
    stream   = aws_sqs_queue.stream_dlq.name
    archive  = aws_sqs_queue.archive_dlq.name
  }
}
