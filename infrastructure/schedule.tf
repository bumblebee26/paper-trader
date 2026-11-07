# Start the server before the 08:45 code pull and stop it after the 15:40 report,
# weekdays only, in IST. Cron on the server keeps doing the actual work.

data "aws_iam_policy_document" "scheduler_assume" {
  count = var.enable_schedule ? 1 : 0
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  count              = var.enable_schedule ? 1 : 0
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume[0].json
}

data "aws_iam_policy_document" "scheduler_ec2" {
  count = var.enable_schedule ? 1 : 0
  statement {
    actions   = ["ec2:StartInstances", "ec2:StopInstances"]
    resources = [aws_instance.trader.arn]
  }
}

resource "aws_iam_role_policy" "scheduler_ec2" {
  count  = var.enable_schedule ? 1 : 0
  name   = "start-stop-paper-trader"
  role   = aws_iam_role.scheduler[0].id
  policy = data.aws_iam_policy_document.scheduler_ec2[0].json
}

resource "aws_scheduler_schedule" "start" {
  count                        = var.enable_schedule ? 1 : 0
  name                         = "${local.name}-start"
  schedule_expression          = "cron(30 8 ? * MON-FRI *)"
  schedule_expression_timezone = "Asia/Kolkata"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:startInstances"
    role_arn = aws_iam_role.scheduler[0].arn
    input    = jsonencode({ InstanceIds = [aws_instance.trader.id] })
  }
}

resource "aws_scheduler_schedule" "stop" {
  count                        = var.enable_schedule ? 1 : 0
  name                         = "${local.name}-stop"
  schedule_expression          = "cron(15 16 ? * MON-FRI *)"
  schedule_expression_timezone = "Asia/Kolkata"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:stopInstances"
    role_arn = aws_iam_role.scheduler[0].arn
    input    = jsonencode({ InstanceIds = [aws_instance.trader.id] })
  }
}
