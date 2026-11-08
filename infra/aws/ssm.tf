# Fine-grained GitHub token for the kicker (repo paper-trader only, Actions: read and write).
# Terraform only creates a placeholder; set the real value yourself so it never lands in state:
#   aws ssm put-parameter --name /stocks-trader/github-dispatch-token --type SecureString --overwrite --value <token> --region ap-southeast-2
resource "aws_ssm_parameter" "github_token" {
  name        = "/${local.prefix}/github-dispatch-token"
  description = "GitHub token that lets the kicker start the trading-session workflow. Real value set outside Terraform."
  type        = "SecureString"
  value       = "SET_ME_OUTSIDE_TERRAFORM"

  lifecycle {
    ignore_changes = [value]
  }
}
