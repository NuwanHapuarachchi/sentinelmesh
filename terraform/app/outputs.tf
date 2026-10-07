output "region" {
  value = var.region
}

output "tables" {
  value = module.shared.tables
}

output "buckets" {
  value = module.shared.buckets
}
