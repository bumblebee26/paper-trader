# All times IST, Monday-Friday. GitHub's own cron (in the workflow) is the
# backup if the kicker is off or fails; a session claims its phase once per day.
# export: the last 4 days' journals go to the lakehouse volume (backup feed + analysis); late: the same at
# 23:30, so ratings given in the evening (or at the weekend, picked up Monday) reach the lakehouse.
locals {
  kicks = {
    morning   = "cron(55 8 ? * MON-FRI *)" # 08:55 from 25 Nov (was 09:08): plan before the pre-open auction ends; rollback = cron(8 9 ...)
    afternoon = "cron(27 12 ? * MON-FRI *)"
  }

  guards = {
    scanner_confirm = "cron(45 9 ? * MON-FRI *)" # the morning's technical-scanner picks after the first half hour
    squareoff       = "cron(12 15 ? * MON-FRI *)"
    park            = "cron(14 15 ? * MON-FRI *)"
    report          = "cron(40 15 ? * MON-FRI *)"
    export          = "cron(45 15 ? * MON-FRI *)"
    swing_shadow    = "cron(50 15 ? * MON-FRI *)" # swing pool's shadow tests (news breakouts, technical scanner) vs NIFTYBEES
    late            = "cron(30 23 ? * MON-FRI *)" # second export, so evening ratings reach the lakehouse overnight
    weekly          = "cron(55 17 ? * FRI *)"
  }
}

# 16:10: the model shadow job and the council test (GitHub's own cron started it 8-11 hours late)
resource "aws_scheduler_schedule" "models" {
  name        = "${local.prefix}-kick-models"
  description = "Start the Model shadow workflow after the 15:45 export."
  state       = var.dispatch_enabled ? "ENABLED" : "DISABLED"

  schedule_expression          = "cron(10 16 ? * MON-FRI *)"
  schedule_expression_timezone = "Asia/Kolkata"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.kicker.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ workflow = "model-shadow.yml" })
  }
}

resource "aws_scheduler_schedule" "kick" {
  for_each = local.kicks

  name        = "${local.prefix}-kick-${each.key}"
  description = "Start the ${each.key} GitHub Actions trading session."
  state       = var.dispatch_enabled ? "ENABLED" : "DISABLED"

  schedule_expression          = each.value
  schedule_expression_timezone = "Asia/Kolkata"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.kicker.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ phase = each.key })

    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 600
    }
  }
}

resource "aws_scheduler_schedule" "guard" {
  for_each = local.guards

  name        = "${local.prefix}-guard-${each.key}"
  description = "Guardian backstop: ${each.key}."
  state       = var.guardian_enabled ? "ENABLED" : "DISABLED"

  schedule_expression          = each.value
  schedule_expression_timezone = "Asia/Kolkata"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.guardian.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ action = each.key })

    retry_policy {
      maximum_retry_attempts       = 1
      maximum_event_age_in_seconds = 600
    }
  }
}

# Every 2 minutes, all week. The handler exits at once while a session is live
# (~1,700 of ~21,600 runs a month do any work), well inside the Lambda free tier.
resource "aws_scheduler_schedule" "responder" {
  name        = "${local.prefix}-responder"
  description = "Answer Telegram commands when no trading session is running."
  state       = var.responder_enabled ? "ENABLED" : "DISABLED"

  schedule_expression          = "rate(2 minutes)"
  schedule_expression_timezone = "Asia/Kolkata"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.responder.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({})

    retry_policy {
      maximum_retry_attempts       = 0
      maximum_event_age_in_seconds = 60
    }
  }
}
