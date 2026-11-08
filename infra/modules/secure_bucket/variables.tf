variable "bucket_name" {
  description = "Globally unique S3 bucket name."
  type        = string
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete a non-empty bucket. Keep false for anything you care about."
  type        = bool
  default     = false
}

variable "noncurrent_version_expiration_days" {
  description = "Days to keep overwritten or deleted object versions before they are removed."
  type        = number
  default     = 30
}
