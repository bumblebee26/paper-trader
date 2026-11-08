data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

# Read/write the trader state bucket (no delete).
data "aws_iam_policy_document" "state_rw" {
  statement {
    sid       = "ListStateBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [module.state.arn]
  }

  statement {
    sid       = "ReadWriteState"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${module.state.arn}/*"]
  }
}

# ------------------------------------------------------------------ guardian --
resource "aws_iam_role" "guardian" {
  name               = "${local.prefix}-guardian-role"
  description        = "Guardian Lambda: trader state bucket + Telegram settings. Nothing else."
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "guardian_logs" {
  role       = aws_iam_role.guardian.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "guardian" {
  source_policy_documents = [data.aws_iam_policy_document.state_rw.json]

  statement {
    sid       = "ReadTelegramSettings"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = local.telegram_param_arns
  }
}

resource "aws_iam_role_policy" "guardian" {
  name   = "state-and-telegram"
  role   = aws_iam_role.guardian.id
  policy = data.aws_iam_policy_document.guardian.json
}

# -------------------------------------------------------------------- kicker --
resource "aws_iam_role" "kicker" {
  name               = "${local.prefix}-kicker-role"
  description        = "Kicker Lambda: read the GitHub dispatch token. Nothing else."
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "kicker_logs" {
  role       = aws_iam_role.kicker.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "kicker" {
  statement {
    sid       = "ReadGithubToken"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.github_token.arn]
  }
}

resource "aws_iam_role_policy" "kicker" {
  name   = "read-github-token"
  role   = aws_iam_role.kicker.id
  policy = data.aws_iam_policy_document.kicker.json
}

# ----------------------------------------------------------------- scheduler --
resource "aws_iam_role" "scheduler" {
  name               = "${local.prefix}-scheduler-role"
  description        = "EventBridge Scheduler: invoke the kicker and guardian Lambdas only."
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "InvokeTraderLambdas"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.kicker.arn, aws_lambda_function.guardian.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "invoke-trader-lambdas"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}
