# Field devices: each simulated station is an IoT thing with its own X.509 certificate. The policy lets a
# certificate connect only as its own thing and publish only to sm/dev/{its zone}/{its class}, so a stolen
# or spoofed device can at most speak for one sensor, and revoking its certificate silences it (its zone
# then turns blind). The cell rules take the class and source from the topic and certificate, never from
# the payload (modules/cell/observations.tf).
#
# The private keys are generated here, so they sit in the Terraform state (GitLab-managed, encrypted) and in
# Secrets Manager for edge/field_gateway.py. Fine for simulated stations; real stations would generate keys
# on the device and send only a certificate request.

locals {
  zone_catalogue = { for z in local.zones : z.zone_id => z }
  devices = {
    "${var.prefix}-gauge-Z07" = { zone = "Z07", class = "gauge" }
    "${var.prefix}-gauge-Z35" = { zone = "Z35", class = "gauge" }
    "${var.prefix}-gauge-T01" = { zone = "T01", class = "gauge" }
  }
}

resource "aws_iot_thing" "device" {
  for_each = local.devices
  name     = each.key
  attributes = {
    zone  = each.value.zone
    class = each.value.class
  }
}

resource "tls_private_key" "device" {
  for_each    = local.devices
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_cert_request" "device" {
  for_each        = local.devices
  private_key_pem = tls_private_key.device[each.key].private_key_pem

  subject {
    common_name = each.key
  }
}

resource "aws_iot_certificate" "device" {
  for_each = local.devices
  csr      = tls_cert_request.device[each.key].cert_request_pem
  active   = !contains(var.revoked_devices, each.key)
}

resource "aws_iot_policy" "device" {
  name = "${var.prefix}-field-device"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "iot:Connect"
        Resource = "arn:aws:iot:${var.region}:${var.account_id}:client/$${iot:Connection.Thing.ThingName}"
        Condition = {
          Bool = { "iot:Connection.Thing.IsAttached" = "true" }
        }
      },
      {
        Effect   = "Allow"
        Action   = "iot:Publish"
        Resource = "arn:aws:iot:${var.region}:${var.account_id}:topic/sm/dev/$${iot:Connection.Thing.Attributes[zone]}/$${iot:Connection.Thing.Attributes[class]}"
      },
    ]
  })
}

resource "aws_iot_policy_attachment" "device" {
  for_each = local.devices
  policy   = aws_iot_policy.device.name
  target   = aws_iot_certificate.device[each.key].arn
}

resource "aws_iot_thing_principal_attachment" "device" {
  for_each  = local.devices
  thing     = aws_iot_thing.device[each.key].name
  principal = aws_iot_certificate.device[each.key].arn
}

# What edge/field_gateway.py needs to run a station: its certificate and key, the endpoint, and its zone's
# thresholds for the demo profiles.
resource "aws_secretsmanager_secret" "device" {
  for_each                = local.devices
  name                    = "${var.prefix}/devices/${each.key}"
  description             = "Credentials for simulated field station ${each.key}"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "device" {
  for_each  = local.devices
  secret_id = aws_secretsmanager_secret.device[each.key].id
  secret_string = jsonencode({
    thing           = each.key
    zone            = each.value.zone
    class           = each.value.class
    endpoint        = data.aws_iot_endpoint.data.endpoint_address
    certificate_pem = aws_iot_certificate.device[each.key].certificate_pem
    private_key_pem = tls_private_key.device[each.key].private_key_pem
    warning_level_m = local.zone_catalogue[each.value.zone].warning_level_m
    danger_level_m  = local.zone_catalogue[each.value.zone].danger_level_m
  })
}
