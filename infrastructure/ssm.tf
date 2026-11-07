# Secrets and settings the server reads at boot. SecureString = encrypted at rest
# with the AWS-managed KMS key. Note: values also live in Terraform state, so keep
# the state file private (it is gitignored).

resource "aws_ssm_parameter" "telegram_bot_token" {
  name  = "${local.param_prefix}/telegram_bot_token"
  type  = "SecureString"
  value = var.telegram_bot_token
}

# Only the credential you provide is stored (SSM can't hold empty values).
resource "aws_ssm_parameter" "anthropic_api_key" {
  count = var.anthropic_api_key == "" ? 0 : 1
  name  = "${local.param_prefix}/anthropic_api_key"
  type  = "SecureString"
  value = var.anthropic_api_key
}

resource "aws_ssm_parameter" "claude_oauth_token" {
  count = var.claude_oauth_token == "" ? 0 : 1
  name  = "${local.param_prefix}/claude_oauth_token"
  type  = "SecureString"
  value = var.claude_oauth_token
}

resource "aws_ssm_parameter" "telegram_user_id" {
  name  = "${local.param_prefix}/telegram_user_id"
  type  = "String"
  value = var.telegram_user_id
}

resource "aws_ssm_parameter" "deploy_key_private" {
  name  = "${local.param_prefix}/github_deploy_key"
  type  = "SecureString"
  value = tls_private_key.deploy.private_key_openssh
}
