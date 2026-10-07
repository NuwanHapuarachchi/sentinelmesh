# Zero-retention rules for every table: no deletion protection, no point-in-time recovery
# (PITR would leave a 35-day backup after delete), AWS-owned encryption.

resource "aws_dynamodb_table" "zones" {
  name                        = "${var.prefix}-zones"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "zone_id"
  deletion_protection_enabled = false

  attribute {
    name = "zone_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }
}

resource "aws_dynamodb_table" "zone_state" {
  name                        = "${var.prefix}-zone-state"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "zone_id"
  deletion_protection_enabled = false

  attribute {
    name = "zone_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }
}

# Seed the zone catalogue. owner_cell changes at runtime during a failover drill;
# the next apply resets it to the catalogue value.
resource "aws_dynamodb_table_item" "zone" {
  for_each = { for z in var.zones : z.zone_id => z }

  table_name = aws_dynamodb_table.zones.name
  hash_key   = aws_dynamodb_table.zones.hash_key
  item = jsonencode({
    zone_id            = { S = each.value.zone_id }
    name               = { S = each.value.name }
    district           = { S = each.value.district }
    hazard             = { S = each.value.hazard }
    lat                = { N = tostring(each.value.lat) }
    lon                = { N = tostring(each.value.lon) }
    polygon            = { S = jsonencode(each.value.polygon) }
    owner_cell         = { S = each.value.owner_cell }
    warning_level_m    = { N = tostring(each.value.warning_level_m) }
    danger_level_m     = { N = tostring(each.value.danger_level_m) }
    gauge_cadence_s    = { N = tostring(each.value.gauge_cadence_s) }
    approval_timeout_s = { N = tostring(each.value.approval_timeout_s) }
    is_test            = { BOOL = each.value.is_test }
  })
}
