locals {
  zones = jsondecode(file("${path.module}/data/zones.json"))

  # Created by terraform/bootstrap; every workload role must carry it.
  boundary_arn    = "arn:aws:iam::${var.account_id}:policy/${var.prefix}-workload-boundary"
  deploy_role_arn = "arn:aws:iam::${var.account_id}:role/${var.prefix}-deploy"
}

module "shared" {
  source = "../modules/shared"

  prefix          = var.prefix
  account_id      = var.account_id
  zones           = local.zones
  deploy_role_arn = local.deploy_role_arn
}
