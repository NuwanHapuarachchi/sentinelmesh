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

# ---------- node identities ----------
# Each district's node is an IoT thing with its own certificate. It may connect only as itself and receive
# only its district's alert topic. Its certificate also buys short-lived AWS credentials through an IoT role
# alias (as Greengrass does), scoped by thing name to its own bundle and its own decision uploads.

data "aws_iot_endpoint" "credentials" {
  endpoint_type = "iot:CredentialProvider"
}

locals {
  districts = toset(distinct([for z in local.zones : z.district if !z.is_test]))
  nodes = { for d in local.districts : "${var.prefix}-node-${trim(replace(lower(d), "/[^a-z0-9]+/", "-"), "-")}" => {
    district = d
    slug     = trim(replace(lower(d), "/[^a-z0-9]+/", "-"), "-")
  } }
}

resource "aws_iot_thing" "node" {
  for_each = local.nodes
  name     = each.key
  attributes = {
    district = each.value.slug
  }
}

resource "tls_private_key" "node" {
  for_each    = local.nodes
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_cert_request" "node" {
  for_each        = local.nodes
  private_key_pem = tls_private_key.node[each.key].private_key_pem

  subject {
    common_name = each.key
  }
}

resource "aws_iot_certificate" "node" {
  for_each = local.nodes
  csr      = tls_cert_request.node[each.key].cert_request_pem
  active   = true
}

resource "aws_iam_role" "node" {
  name                 = "${var.prefix}-district-node"
  path                 = local.role_path
  permissions_boundary = local.boundary_arn
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "credentials.iot.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "node" {
  name = "district-node"
  role = aws_iam_role.node.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${module.shared.bucket_arns.evidence}/edge/$${credentials-iot:ThingName}/*"
      },
      {
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = ["${module.shared.bucket_arns.decisions}/decisions/edge/$${credentials-iot:ThingName}/*", "${module.shared.bucket_arns.decisions}/decisions/chain/*"]
      },
      {
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${module.shared.bucket_arns.decisions}/decisions/chain/*"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
        Resource = local.a.meta
      },
    ]
  })
}

resource "aws_iot_role_alias" "node" {
  alias               = "${var.prefix}-district-node"
  role_arn            = aws_iam_role.node.arn
  credential_duration = 3600
}

resource "aws_iot_policy" "node" {
  name = "${var.prefix}-district-node"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "iot:Connect"
        Resource  = "arn:aws:iot:${var.region}:${var.account_id}:client/$${iot:Connection.Thing.ThingName}"
        Condition = { Bool = { "iot:Connection.Thing.IsAttached" = "true" } }
      },
      {
        Effect   = "Allow"
        Action   = "iot:Subscribe"
        Resource = "arn:aws:iot:${var.region}:${var.account_id}:topicfilter/sm/alerts/$${iot:Connection.Thing.Attributes[district]}"
      },
      {
        Effect   = "Allow"
        Action   = "iot:Receive"
        Resource = "arn:aws:iot:${var.region}:${var.account_id}:topic/sm/alerts/$${iot:Connection.Thing.Attributes[district]}"
      },
      {
        Effect   = "Allow"
        Action   = "iot:AssumeRoleWithCertificate"
        Resource = aws_iot_role_alias.node.arn
      },
    ]
  })
}

resource "aws_iot_policy_attachment" "node" {
  for_each = local.nodes
  policy   = aws_iot_policy.node.name
  target   = aws_iot_certificate.node[each.key].arn
}

resource "aws_iot_thing_principal_attachment" "node" {
  for_each  = local.nodes
  thing     = aws_iot_thing.node[each.key].name
  principal = aws_iot_certificate.node[each.key].arn
}

# What edge/local_alert_node.py needs: its certificate and key, the endpoints and where its bundle lives.
resource "aws_secretsmanager_secret" "node" {
  for_each                = local.nodes
  name                    = "${var.prefix}/devices/${each.key}"
  description             = "Credentials for the ${each.value.district} Local Alert Node"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "node" {
  for_each  = local.nodes
  secret_id = aws_secretsmanager_secret.node[each.key].id
  secret_string = jsonencode({
    thing                = each.key
    district             = each.value.district
    slug                 = each.value.slug
    region               = var.region
    endpoint             = data.aws_iot_endpoint.data.endpoint_address
    credentials_endpoint = data.aws_iot_endpoint.credentials.endpoint_address
    role_alias           = aws_iot_role_alias.node.alias
    evidence_bucket      = module.shared.buckets.evidence
    decisions_bucket     = module.shared.buckets.decisions
    meta_table           = local.t.meta
    certificate_pem      = aws_iot_certificate.node[each.key].certificate_pem
    private_key_pem      = tls_private_key.node[each.key].private_key_pem
  })
}
