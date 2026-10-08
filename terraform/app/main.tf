data "aws_caller_identity" "current" {}

data "aws_iam_session_context" "pipeline" {
  arn = data.aws_caller_identity.current.arn
}

locals {
  zones = jsondecode(file("${path.module}/data/zones.json"))

  boundary_arn = aws_iam_policy.workload_boundary.arn

  # Principal allowed to delete decision records (teardown): whoever runs Terraform, as an IAM
  # user or role ARN (session ARNs are resolved to their role).
  deploy_role_arn = data.aws_iam_session_context.pipeline.issuer_arn
}

module "shared" {
  source = "../modules/shared"

  prefix          = var.prefix
  account_id      = var.account_id
  zones           = local.zones
  deploy_role_arn = local.deploy_role_arn

  web_distribution_arn = aws_cloudfront_distribution.web.arn
}

# One zip for every function; handlers differ. The plan job passes build/ to the apply job.
data "archive_file" "services" {
  type             = "zip"
  source_dir       = "${path.module}/../../services"
  output_path      = "${path.module}/build/services.zip"
  output_file_mode = "0666"
  excludes         = ["**/__pycache__/**", "**/*.pyc"]
}

locals {
  role_path = "/${var.prefix}/"

  # a and b own the zones; c is the warm standby: fully deployed, owns no zones until a failover, and
  # proven warm by its own heartbeat zone (H02). Idle serverless cells cost next to nothing.
  active_cells    = ["a", "b"]
  standby_cell    = "c"
  cells           = toset(concat(local.active_cells, [local.standby_cell]))
  heartbeat_zones = { H00 = "a", H01 = "b", H02 = "c" }
}

module "cell" {
  source   = "../modules/cell"
  for_each = local.cells

  cell          = each.key
  prefix        = var.prefix
  role_path     = local.role_path
  boundary_arn  = local.boundary_arn
  zip_path      = data.archive_file.services.output_path
  zip_hash      = data.archive_file.services.output_base64sha256
  zones_table   = { name = module.shared.tables.zones, arn = module.shared.table_arns.zones }
  state_table   = { name = module.shared.tables.zone_state, arn = module.shared.table_arns.zone_state }
  delivery      = local.delivery
  sms_config    = local.sms_config
  rules_version = var.rules_version

  evidence_bucket = { name = module.shared.buckets.evidence, arn = module.shared.bucket_arns.evidence }
  public_feed     = { name = module.public_feed.name, arn = module.public_feed.arn }
}
