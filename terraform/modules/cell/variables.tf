variable "cell" {
  description = "Cell id (a, b)."
  type        = string
}

variable "prefix" {
  type = string
}

variable "role_path" {
  type = string
}

variable "boundary_arn" {
  type = string
}

variable "zip_path" {
  type = string
}

variable "zip_hash" {
  type = string
}

variable "zones_table" {
  type = object({ name = string, arn = string })
}

variable "state_table" {
  type = object({ name = string, arn = string })
}
