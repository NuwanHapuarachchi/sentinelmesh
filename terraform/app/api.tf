# REST API (regional) + WAF + Cognito. Routes come from one map; the OpenAPI body is generated.

resource "random_password" "report_pepper" {
  length  = 32
  special = false
}

data "aws_iot_endpoint" "data" {
  endpoint_type = "iot:Data-ATS"
}

locals {
  fn_common = {
    zip_path     = data.archive_file.services.output_path
    zip_hash     = data.archive_file.services.output_base64sha256
    boundary_arn = local.boundary_arn
    role_path    = local.role_path
  }
  t = module.shared.tables
  a = module.shared.table_arns
}

module "public_api" {
  source       = "../modules/lambda_fn"
  name         = "${var.prefix}-public-api"
  handler      = "public_api.handler.handler"
  zip_path     = local.fn_common.zip_path
  zip_hash     = local.fn_common.zip_hash
  boundary_arn = local.fn_common.boundary_arn
  role_path    = local.fn_common.role_path

  environment = {
    ZONES_TABLE       = local.t.zones
    STATE_TABLE       = local.t.zone_state
    SUBSCRIBERS_TABLE = local.t.subscribers
    INBOX_TABLE       = local.t.inbox
    ALERTS_TABLE      = local.t.alerts
    RATELIMITS_TABLE  = local.t.ratelimits
    IOT_ENDPOINT      = data.aws_iot_endpoint.data.endpoint_address
    REPORT_PEPPER     = random_password.report_pepper.result
    SMS_SECRET_ARN    = aws_secretsmanager_secret.sms.arn
  }

  policy_statements = [
    { Effect = "Allow", Action = "dynamodb:GetItem", Resource = [local.a.zones, local.a.subscribers] },
    { Effect = "Allow", Action = "dynamodb:Scan", Resource = local.a.zone_state },
    { Effect = "Allow", Action = "dynamodb:PutItem", Resource = local.a.subscribers },
    { Effect = "Allow", Action = ["dynamodb:Query", "dynamodb:UpdateItem"], Resource = local.a.inbox },
    { Effect = "Allow", Action = "dynamodb:UpdateItem", Resource = [local.a.alerts, local.a.ratelimits] },
    { Effect = "Allow", Action = "iot:Publish", Resource = "arn:aws:iot:${var.region}:${var.account_id}:topic/sm/obs/*" },
    { Effect = "Allow", Action = "secretsmanager:GetSecretValue", Resource = aws_secretsmanager_secret.sms.arn },
  ]
}

module "scenario" {
  source       = "../modules/lambda_fn"
  name         = "${var.prefix}-scenario"
  handler      = "scenario.handler.handler"
  zip_path     = local.fn_common.zip_path
  zip_hash     = local.fn_common.zip_hash
  boundary_arn = local.fn_common.boundary_arn
  role_path    = local.fn_common.role_path
  timeout      = 300

  environment = {
    ZONES_TABLE  = local.t.zones
    IOT_ENDPOINT = data.aws_iot_endpoint.data.endpoint_address
  }

  policy_statements = [
    { Effect = "Allow", Action = "dynamodb:GetItem", Resource = local.a.zones },
    { Effect = "Allow", Action = "iot:Publish", Resource = "arn:aws:iot:${var.region}:${var.account_id}:topic/sm/obs/*" },
  ]
}

module "console_api" {
  source       = "../modules/lambda_fn"
  name         = "${var.prefix}-console-api"
  handler      = "console_api.handler.handler"
  zip_path     = local.fn_common.zip_path
  zip_hash     = local.fn_common.zip_hash
  boundary_arn = local.fn_common.boundary_arn
  role_path    = local.fn_common.role_path

  environment = {
    ZONES_TABLE       = local.t.zones
    STATE_TABLE       = local.t.zone_state
    ALERTS_TABLE      = local.t.alerts
    SCENARIO_FUNCTION = module.scenario.name
  }

  policy_statements = [
    { Effect = "Allow", Action = ["dynamodb:Scan", "dynamodb:GetItem", "dynamodb:UpdateItem"], Resource = local.a.zones },
    { Effect = "Allow", Action = "dynamodb:Scan", Resource = local.a.zone_state },
    { Effect = "Allow", Action = "dynamodb:Query", Resource = "${local.a.alerts}/index/by-day" },
    { Effect = "Allow", Action = "lambda:InvokeFunction", Resource = module.scenario.arn },
  ]
}

# ---------- Cognito ----------

resource "aws_cognito_user_pool" "officers" {
  name                     = "${var.prefix}-officers"
  deletion_protection      = "INACTIVE"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_numbers   = true
    require_symbols   = false
    require_uppercase = true
  }
}

resource "aws_cognito_user_pool_domain" "officers" {
  domain       = "${var.prefix}-${var.account_id}"
  user_pool_id = aws_cognito_user_pool.officers.id
}

resource "aws_cognito_user_pool_client" "console" {
  name                                 = "${var.prefix}-console"
  user_pool_id                         = aws_cognito_user_pool.officers.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = ["https://${aws_cloudfront_distribution.web.domain_name}/", "http://localhost:8000/"]
  logout_urls                          = ["https://${aws_cloudfront_distribution.web.domain_name}/", "http://localhost:8000/"]
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"]
  id_token_validity                    = 60
  access_token_validity                = 60
  token_validity_units {
    id_token     = "minutes"
    access_token = "minutes"
  }
}

resource "aws_cognito_user_group" "groups" {
  for_each     = toset(["duty_officer", "viewer"])
  name         = each.key
  user_pool_id = aws_cognito_user_pool.officers.id
}

# Team accounts from TF_VAR_officers = {"email": "duty_officer" | "viewer"}. Cognito emails each person
# a temporary password; nobody else ever sees it.
resource "aws_cognito_user" "officer" {
  for_each     = var.officers
  user_pool_id = aws_cognito_user_pool.officers.id
  username     = each.key
  attributes = {
    email          = each.key
    email_verified = true
  }
  desired_delivery_mediums = ["EMAIL"]
}

resource "aws_cognito_user_in_group" "officer" {
  for_each     = var.officers
  user_pool_id = aws_cognito_user_pool.officers.id
  group_name   = aws_cognito_user_group.groups[each.value].name
  username     = aws_cognito_user.officer[each.key].username
}

# ---------- REST API ----------

locals {
  functions = {
    public    = module.public_api
    console   = module.console_api
    approvals = module.approvals
  }

  # path => { method => function key }; "public" routes have no login, the rest need Cognito.
  routes = {
    "/reports"               = { post = "public" }
    "/public/zones"          = { get = "public" }
    "/phone/subscribe"       = { post = "public" }
    "/phone/inbox"           = { get = "public" }
    "/phone/ack"             = { post = "public" }
    "/sms/dlr"               = { post = "public" }
    "/zones"                 = { get = "console" }
    "/alerts"                = { get = "console" }
    "/scenarios/{id}"        = { post = "console" }
    "/cells/{cell}/failover" = { post = "console" }
    "/cells/{cell}/restore"  = { post = "console" }
    "/approvals"             = { get = "approvals" }
    "/approvals/{id}"        = { post = "approvals" }
  }

  cors_headers = {
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
    "method.response.header.Access-Control-Allow-Headers" = "'Authorization,Content-Type,X-Device-Token'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,POST,OPTIONS'"
  }

  openapi = {
    openapi = "3.0.1"
    info    = { title = "${var.prefix}-api", version = "1.0" }
    components = {
      securitySchemes = {
        cognito = {
          type                           = "apiKey"
          name                           = "Authorization"
          in                             = "header"
          "x-amazon-apigateway-authtype" = "cognito_user_pools"
          "x-amazon-apigateway-authorizer" = {
            type         = "cognito_user_pools"
            providerARNs = [aws_cognito_user_pool.officers.arn]
          }
        }
      }
    }
    paths = { for path, methods in local.routes : path => merge(
      { for method, fn in methods : method => merge(
        {
          "x-amazon-apigateway-integration" = {
            type       = "aws_proxy"
            httpMethod = "POST"
            uri        = "arn:aws:apigateway:${var.region}:lambda:path/2015-03-31/functions/${local.functions[fn].arn}/invocations"
          }
          responses = {}
        },
        fn == "public" ? {} : { security = [{ cognito = [] }] },
      ) },
      {
        options = {
          responses = {
            "200" = { headers = {
              "Access-Control-Allow-Origin"  = { schema = { type = "string" } }
              "Access-Control-Allow-Headers" = { schema = { type = "string" } }
              "Access-Control-Allow-Methods" = { schema = { type = "string" } }
            } }
          }
          "x-amazon-apigateway-integration" = {
            type             = "mock"
            requestTemplates = { "application/json" = "{\"statusCode\": 200}" }
            responses        = { default = { statusCode = "200", responseParameters = local.cors_headers } }
          }
        }
      },
    ) }
    "x-amazon-apigateway-gateway-responses" = {
      DEFAULT_4XX = { responseParameters = { "gatewayresponse.header.Access-Control-Allow-Origin" = "'*'", "gatewayresponse.header.Access-Control-Allow-Headers" = "'Authorization,Content-Type,X-Device-Token'" } }
      DEFAULT_5XX = { responseParameters = { "gatewayresponse.header.Access-Control-Allow-Origin" = "'*'" } }
    }
  }
}

resource "aws_api_gateway_rest_api" "api" {
  name = "${var.prefix}-api"
  body = jsonencode(local.openapi)

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

resource "aws_api_gateway_deployment" "api" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  triggers = {
    redeploy = sha1(jsonencode(local.openapi))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "v1" {
  rest_api_id          = aws_api_gateway_rest_api.api.id
  deployment_id        = aws_api_gateway_deployment.api.id
  stage_name           = "v1"
  xray_tracing_enabled = true
}

resource "aws_api_gateway_method_settings" "all" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  stage_name  = aws_api_gateway_stage.v1.stage_name
  method_path = "*/*"

  settings {
    throttling_burst_limit = 200
    throttling_rate_limit  = 100
    metrics_enabled        = true
  }
}

resource "aws_lambda_permission" "api" {
  for_each      = local.functions
  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = each.value.name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/*/*"
}

# ---------- WAF ----------

resource "aws_wafv2_web_acl" "api" {
  name  = "${var.prefix}-api"
  scope = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "aws-common"
    priority = 1
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "aws-common"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "aws-known-bad-inputs"
    priority = 2
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "aws-known-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  # A flood of fake reports can't manufacture agreement: 10 reports per minute per IP.
  rule {
    name     = "reports-rate"
    priority = 3
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit                 = 10
        evaluation_window_sec = 60
        aggregate_key_type    = "IP"
        scope_down_statement {
          byte_match_statement {
            search_string         = "/reports"
            positional_constraint = "ENDS_WITH"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "reports-rate"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "overall-rate"
    priority = 4
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit                 = 3000
        evaluation_window_sec = 300
        aggregate_key_type    = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "overall-rate"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.prefix}-api"
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "api" {
  resource_arn = aws_api_gateway_stage.v1.arn
  web_acl_arn  = aws_wafv2_web_acl.api.arn
}
