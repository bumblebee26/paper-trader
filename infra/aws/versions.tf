terraform {
  # 1.10+ is needed for S3-native state locking (use_lockfile).
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Partial config: values come from backend.hcl (see backend.hcl.example) or CI.
  backend "s3" {}
}
