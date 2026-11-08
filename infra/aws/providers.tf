provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "paper-trader"
      ManagedBy = "terraform"
      Stack     = "aws"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  prefix     = var.name_prefix
  param_arn  = "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter"

  # Created by the stocks_lakehouse stacks in the same account; referenced by name only.
  lakehouse_params = {
    telegram_token       = "/${var.lakehouse_prefix}/telegram-bot-token"
    telegram_chat_id     = "/${var.lakehouse_prefix}/telegram-chat-id"
    databricks_url       = "/${var.lakehouse_prefix}/databricks-workspace-url"
    databricks_token     = "/${var.lakehouse_prefix}/databricks-pusher-token"
    databricks_warehouse = "/${var.lakehouse_prefix}/databricks-warehouse-id"
  }

  telegram_param_arns = [
    "${local.param_arn}${local.lakehouse_params.telegram_token}",
    "${local.param_arn}${local.lakehouse_params.telegram_chat_id}",
  ]

  databricks_param_arns = [
    "${local.param_arn}${local.lakehouse_params.databricks_url}",
    "${local.param_arn}${local.lakehouse_params.databricks_token}",
    "${local.param_arn}${local.lakehouse_params.databricks_warehouse}",
  ]
}
