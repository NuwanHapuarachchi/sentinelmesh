output "region" {
  value = var.region
}

output "tables" {
  value = module.shared.tables
}

output "buckets" {
  value = module.shared.buckets
}

output "cells" {
  value = { for c, m in module.cell : c => {
    observations_table = m.observations_table
    scorer_function    = m.scorer_function
    scorer_mapping     = m.scorer_mapping_uuid
  } }
}

output "dashboard_url" {
  value = "https://${aws_cloudfront_distribution.web.domain_name}/"
}

output "phone_url" {
  value = "https://${aws_cloudfront_distribution.web.domain_name}/phone.html"
}

output "api_url" {
  value = aws_api_gateway_stage.v1.invoke_url
}

output "cognito_domain" {
  value = "https://${aws_cognito_user_pool_domain.officers.domain}.auth.${var.region}.amazoncognito.com"
}
