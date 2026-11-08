# ---------------------------------------------------------------------------
# Identity for the GitHub Actions trading session (.github/workflows/trading-session.yml).
#
# Terraform plan/apply reuse the stocks_lakehouse CI users (stocks-gha-plan,
# stocks-gha-apply); that is why trader names start with "stocks-". The session
# itself needs a much smaller identity: the free plan denies IAM identity
# providers (no GitHub OIDC), so it is an IAM user whose access key is created
# OUTSIDE Terraform and stored in the GitHub "trading" environment.
#
#   aws iam create-access-key --user-name stocks-gha-trader
# ---------------------------------------------------------------------------

resource "aws_iam_user" "session" {
  name          = "${var.lakehouse_prefix}-gha-trader"
  force_destroy = true # allow destroy even after access keys are added
}

data "aws_iam_policy_document" "session" {
  source_policy_documents = [data.aws_iam_policy_document.state_rw.json]

  statement {
    sid       = "ReadTelegramAndLakehouseSettings"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = concat(local.telegram_param_arns, local.databricks_param_arns)
  }
}

resource "aws_iam_user_policy" "session" {
  name   = "trading-session"
  user   = aws_iam_user.session.name
  policy = data.aws_iam_policy_document.session.json
}
