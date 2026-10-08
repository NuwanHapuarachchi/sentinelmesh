# Local Alert Nodes: one per district, running edge/local_alert_node.py in the district emergency office.
# The cloud pre-stages each node's bundle (zones, thresholds, who to warn) every 5 minutes, so the node can
# warn its district from what it already holds when the cloud is gone (static stability).

module "edge_bundle" {
  source       = "../modules/lambda_fn"
  name         = "${var.prefix}-edge-bundle"
  handler      = "edge_bundle.handler.handler"
  zip_path     = local.fn_common.zip_path
  zip_hash     = local.fn_common.zip_hash
  boundary_arn = local.fn_common.boundary_arn
  role_path    = local.fn_common.role_path
  timeout      = 60

  environment = {
    ZONES_TABLE       = local.t.zones
    SUBSCRIBERS_TABLE = local.t.subscribers
    EVIDENCE_BUCKET   = module.shared.buckets.evidence
    NODE_PREFIX       = "${var.prefix}-node-"
  }

  policy_statements = [
    { Effect = "Allow", Action = "dynamodb:Scan", Resource = local.a.zones },
    { Effect = "Allow", Action = "dynamodb:Query", Resource = "${local.a.subscribers}/index/by-zone" },
    { Effect = "Allow", Action = "s3:PutObject", Resource = "${module.shared.bucket_arns.evidence}/edge/*" },
  ]
}

resource "aws_scheduler_schedule" "edge_bundle" {
  name                = "${var.prefix}-edge-bundle"
  schedule_expression = "rate(5 minutes)"
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = module.edge_bundle.arn
    role_arn = aws_iam_role.ops_scheduler.arn
  }
}
