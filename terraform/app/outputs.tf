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
