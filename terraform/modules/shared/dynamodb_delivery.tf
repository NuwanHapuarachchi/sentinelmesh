# Delivery-side tables. Same zero-retention rules as dynamodb.tf.

resource "aws_dynamodb_table" "alerts" {
  name                        = "${var.prefix}-alerts"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "alert_id"
  range_key                   = "sk"
  deletion_protection_enabled = false

  attribute {
    name = "alert_id"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  attribute {
    name = "day"
    type = "S"
  }

  attribute {
    name = "issued_at"
    type = "N"
  }

  # Recent alerts for the dashboard and the CAP feed (META items only).
  global_secondary_index {
    name            = "by-day"
    hash_key        = "day"
    range_key       = "issued_at"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = false
  }
}

resource "aws_dynamodb_table" "approvals" {
  name                        = "${var.prefix}-approvals"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "approval_id"
  deletion_protection_enabled = false

  attribute {
    name = "approval_id"
    type = "S"
  }

  attribute {
    name = "pending"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "N"
  }

  # Sparse: only PENDING approvals carry the "pending" attribute.
  global_secondary_index {
    name            = "pending"
    hash_key        = "pending"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = false
  }
}

# Residents who opted in. Only a ~1 km geohash cell and zone are kept, never coordinates.
resource "aws_dynamodb_table" "subscribers" {
  name                        = "${var.prefix}-subscribers"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "subscriber_id"
  deletion_protection_enabled = false

  attribute {
    name = "subscriber_id"
    type = "S"
  }

  attribute {
    name = "zone_id"
    type = "S"
  }

  global_secondary_index {
    name            = "by-zone"
    hash_key        = "zone_id"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = false
  }
}

# Delivery ledger: one item per alert and recipient. Senders claim a recipient here with a conditional
# write before sending, so retries never text anyone twice; receipts mark it DELIVERED, so the delivery
# rate of an alert is one query.
resource "aws_dynamodb_table" "deliveries" {
  name                        = "${var.prefix}-deliveries"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "alert_id"
  range_key                   = "subscriber_id"
  deletion_protection_enabled = false

  attribute {
    name = "alert_id"
    type = "S"
  }

  attribute {
    name = "subscriber_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = false
  }
}

# Small shared settings, keyed by "k". Holds the decision chain's head hint (common/chain.py).
resource "aws_dynamodb_table" "meta" {
  name                        = "${var.prefix}-meta"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "k"
  deletion_protection_enabled = false

  attribute {
    name = "k"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }
}

# Simulated handset inbox (stands in for SMS / cell broadcast in the demo).
resource "aws_dynamodb_table" "inbox" {
  name                        = "${var.prefix}-inbox"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "subscriber_id"
  range_key                   = "sk"
  deletion_protection_enabled = false

  attribute {
    name = "subscriber_id"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = false
  }
}

resource "aws_dynamodb_table" "ratelimits" {
  name                        = "${var.prefix}-ratelimits"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "k"
  deletion_protection_enabled = false

  attribute {
    name = "k"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = false
  }
}
