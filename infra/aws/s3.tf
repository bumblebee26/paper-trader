# Dummy ledger, journal, day plans and reports. GitHub Actions sessions and the
# guardian Lambda share it (a runner forgets everything when the job ends).
module "state" {
  source = "../modules/secure_bucket"

  bucket_name                        = "${local.prefix}-state-${local.account_id}"
  noncurrent_version_expiration_days = var.state_noncurrent_version_days
}
