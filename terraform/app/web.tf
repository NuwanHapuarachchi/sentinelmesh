# Dashboard, officer console, simulated phone and the public CAP feed, from the private web
# bucket through CloudFront (origin access control; the bucket is never public).

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${var.prefix}-web"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Security headers on every response. The CSP allows exactly what the pages load: scripts from the site
# and cdnjs (Leaflet), styles also from Google Fonts (inline styles are allowed for the evidence bars),
# fonts from Google, map tiles from Esri, and calls to this stack's API and Cognito domain.
locals {
  api_origin     = "https://${aws_api_gateway_rest_api.api.id}.execute-api.${var.region}.amazonaws.com"
  cognito_origin = "https://${aws_cognito_user_pool_domain.officers.domain}.auth.${var.region}.amazoncognito.com"
  csp = join("; ", [
    "default-src 'self'",
    "script-src 'self' https://cdnjs.cloudflare.com",
    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data: https://server.arcgisonline.com https://cdnjs.cloudflare.com",
    "connect-src 'self' ${local.api_origin} ${local.cognito_origin}",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
  ])
}

resource "aws_cloudfront_response_headers_policy" "web" {
  name    = "${var.prefix}-web-security"
  comment = "SentinelMesh security headers"

  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
    }
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    content_security_policy {
      content_security_policy = local.csp
      override                = true
    }
  }
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  comment             = "SentinelMesh web"
  default_root_object = "index.html"
  price_class         = "PriceClass_All"
  wait_for_deployment = false

  origin {
    domain_name              = "${module.shared.buckets.web}.s3.${var.region}.amazonaws.com"
    origin_id                = "web"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  default_cache_behavior {
    target_origin_id           = "web"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # Managed-CachingDisabled: demo pages update often
    response_headers_policy_id = aws_cloudfront_response_headers_policy.web.id
  }

  # Cell-broadcast stand-in objects change every alert; CAP files are immutable.
  ordered_cache_behavior {
    path_pattern               = "cap/*"
    target_origin_id           = "web"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = "658327ea-f89d-4fab-a63d-7e88639e58f6" # Managed-CachingOptimized
    response_headers_policy_id = aws_cloudfront_response_headers_policy.web.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

locals {
  web_dir = "${path.module}/../../web"
  content_types = {
    html    = "text/html; charset=utf-8"
    js      = "application/javascript"
    css     = "text/css"
    json    = "application/json"
    geojson = "application/geo+json"
    svg     = "image/svg+xml"
  }
}

resource "aws_s3_object" "web" {
  for_each     = fileset(local.web_dir, "**/*.{html,js,css,geojson,svg}")
  bucket       = module.shared.buckets.web
  key          = each.value
  source       = "${local.web_dir}/${each.value}"
  etag         = filemd5("${local.web_dir}/${each.value}")
  content_type = lookup(local.content_types, reverse(split(".", each.value))[0], "application/octet-stream")
}

resource "aws_s3_object" "config" {
  bucket       = module.shared.buckets.web
  key          = "config.json"
  content_type = "application/json"
  content = jsonencode({
    api_url        = aws_api_gateway_stage.v1.invoke_url
    cognito_domain = "https://${aws_cognito_user_pool_domain.officers.domain}.auth.${var.region}.amazoncognito.com"
    client_id      = aws_cognito_user_pool_client.console.id
    region         = var.region
  })
}
