# Kicker + guardian. Both ship in one zip built from ../../trader (stdlib + boto3 only).

data "archive_file" "trader" {
  type        = "zip"
  source_dir  = "${path.module}/../../trader"
  output_path = "${path.module}/build/trader.zip"
  excludes    = ["__pycache__", "server.py"]
}

resource "aws_cloudwatch_log_group" "guardian" {
  name              = "/aws/lambda/${local.prefix}-guardian"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "guardian" {
  function_name = "${local.prefix}-guardian"
  description   = "No Claude: 15:12 square-off, 15:40 report, 15:45 export to the lakehouse."
  role          = aws_iam_role.guardian.arn

  runtime       = var.lambda_python_runtime
  architectures = ["arm64"]
  handler       = "handlers.guardian"
  timeout       = 300
  memory_size   = 256

  filename         = data.archive_file.trader.output_path
  source_code_hash = data.archive_file.trader.output_base64sha256

  environment {
    variables = {
      STATE_BUCKET               = module.state.id
      TELEGRAM_TOKEN_PARAM       = local.lakehouse_params.telegram_token
      TELEGRAM_CHAT_ID_PARAM     = local.lakehouse_params.telegram_chat_id
      DATABRICKS_URL_PARAM       = local.lakehouse_params.databricks_url
      DATABRICKS_TOKEN_PARAM     = local.lakehouse_params.databricks_token
      DATABRICKS_WAREHOUSE_PARAM = local.lakehouse_params.databricks_warehouse
      LANDING_VOLUME_PATH        = var.lakehouse_landing_volume
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.guardian,
    aws_iam_role_policy_attachment.guardian_logs,
    aws_iam_role_policy.guardian,
  ]
}

resource "aws_cloudwatch_log_group" "kicker" {
  name              = "/aws/lambda/${local.prefix}-kicker"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "kicker" {
  function_name = "${local.prefix}-kicker"
  description   = "Starts the GitHub Actions trading session at an exact IST time."
  role          = aws_iam_role.kicker.arn

  runtime       = var.lambda_python_runtime
  architectures = ["arm64"]
  handler       = "handlers.kicker"
  timeout       = 60
  memory_size   = 128

  filename         = data.archive_file.trader.output_path
  source_code_hash = data.archive_file.trader.output_base64sha256

  environment {
    variables = {
      DISPATCH_REPO        = var.github_repo
      DISPATCH_REF         = "main"
      DISPATCH_TOKEN_PARAM = aws_ssm_parameter.github_token.name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.kicker,
    aws_iam_role_policy_attachment.kicker_logs,
    aws_iam_role_policy.kicker,
  ]
}

# Answers Telegram commands outside sessions. Same permissions as the guardian
# (state bucket + Telegram settings), so it reuses that role.
resource "aws_cloudwatch_log_group" "responder" {
  name              = "/aws/lambda/${local.prefix}-responder"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "responder" {
  function_name = "${local.prefix}-responder"
  description   = "Answers Telegram commands (/help, /ledger, /plan, /pause) when no session is live."
  role          = aws_iam_role.guardian.arn

  runtime       = var.lambda_python_runtime
  architectures = ["arm64"]
  handler       = "handlers.responder"
  timeout       = 110
  memory_size   = 128

  filename         = data.archive_file.trader.output_path
  source_code_hash = data.archive_file.trader.output_base64sha256

  environment {
    variables = {
      STATE_BUCKET               = module.state.id
      TELEGRAM_TOKEN_PARAM       = local.lakehouse_params.telegram_token
      TELEGRAM_CHAT_ID_PARAM     = local.lakehouse_params.telegram_chat_id
      DATABRICKS_URL_PARAM       = local.lakehouse_params.databricks_url
      DATABRICKS_TOKEN_PARAM     = local.lakehouse_params.databricks_token
      DATABRICKS_WAREHOUSE_PARAM = local.lakehouse_params.databricks_warehouse
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.responder,
    aws_iam_role_policy_attachment.guardian_logs,
    aws_iam_role_policy.guardian,
  ]
}
