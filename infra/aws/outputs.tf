output "state_bucket" {
  description = "Set as the GitHub repository variable TRADER_STATE_BUCKET."
  value       = module.state.id
}

output "session_user" {
  description = "Create one access key for it and store it in the GitHub 'trading' environment."
  value       = aws_iam_user.session.name
}

output "github_token_param" {
  description = "Put a fine-grained GitHub token here before setting dispatch_enabled = true."
  value       = aws_ssm_parameter.github_token.name
}

output "functions" {
  value = {
    guardian  = aws_lambda_function.guardian.function_name
    kicker    = aws_lambda_function.kicker.function_name
    responder = aws_lambda_function.responder.function_name
  }
}
