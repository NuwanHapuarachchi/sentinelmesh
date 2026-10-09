locals {
  buckets = {
    evidence  = "${var.prefix}-evidence-${var.account_id}"  # raw observation archive (JSONL.gz) for Athena replay
    decisions = "${var.prefix}-decisions-${var.account_id}" # alert decision records: write-once, no deletes
    web       = "${var.prefix}-web-${var.account_id}"       # dashboard, phone page and CAP feed behind CloudFront
  }
}

# force_destroy empties every bucket on destroy; nothing uses Object Lock or lifecycle retention.
resource "aws_s3_bucket" "this" {
  for_each      = local.buckets
  bucket        = each.value
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Storage limitation: raw observations are kept 90 days for post-event replay, Athena results a week.
# Decision records and CAP files have no expiry: they are the audit trail.
resource "aws_s3_bucket_lifecycle_configuration" "evidence" {
  bucket = aws_s3_bucket.this["evidence"].id

  rule {
    id     = "observations-90-days"
    status = "Enabled"
    filter {
      prefix = "obs/"
    }
    expiration {
      days = 90
    }
  }

  rule {
    id     = "athena-results-7-days"
    status = "Enabled"
    filter {
      prefix = "athena-results/"
    }
    expiration {
      days = 7
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_versioning" "decisions" {
  bucket = aws_s3_bucket.this["decisions"].id
  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_iam_policy_document" "tls_only" {
  for_each = aws_s3_bucket.this

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [each.value.arn, "${each.value.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  # The web bucket is private; only our CloudFront distribution may read it.
  dynamic "statement" {
    for_each = each.key == "web" ? [1] : []
    content {
      sid       = "CloudFrontRead"
      effect    = "Allow"
      actions   = ["s3:GetObject"]
      resources = ["${each.value.arn}/*"]
      principals {
        type        = "Service"
        identifiers = ["cloudfront.amazonaws.com"]
      }
      condition {
        test     = "StringEquals"
        variable = "AWS:SourceArn"
        values   = [var.web_distribution_arn]
      }
    }
  }

  # Decision records are tamper-resistant without retention dates: new objects only
  # (conditional writes), and only the pipeline role may delete (for teardown).
  dynamic "statement" {
    for_each = each.key == "decisions" ? [1] : []
    content {
      sid       = "DenyOverwrite"
      effect    = "Deny"
      actions   = ["s3:PutObject"]
      resources = ["${each.value.arn}/*"]
      principals {
        type        = "*"
        identifiers = ["*"]
      }
      condition {
        test     = "Null"
        variable = "s3:if-none-match"
        values   = ["true"]
      }
    }
  }

  dynamic "statement" {
    for_each = each.key == "decisions" ? [1] : []
    content {
      sid       = "DenyDeleteExceptPipeline"
      effect    = "Deny"
      actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
      resources = ["${each.value.arn}/*"]
      principals {
        type        = "*"
        identifiers = ["*"]
      }
      condition {
        test     = "ArnNotEquals"
        variable = "aws:PrincipalArn"
        values   = [var.deploy_role_arn]
      }
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  for_each   = aws_s3_bucket.this
  bucket     = each.value.id
  policy     = data.aws_iam_policy_document.tls_only[each.key].json
  depends_on = [aws_s3_bucket_public_access_block.this]
}
