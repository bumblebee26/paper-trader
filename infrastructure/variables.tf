variable "aws_region" {
  description = "AWS region. Mumbai keeps latency to NSE data and Telegram low."
  type        = string
  default     = "ap-south-1"
}

variable "instance_type" {
  description = "EC2 size. 2 GB RAM (t3.small) is the minimum that runs Claude Code comfortably."
  type        = string
  default     = "t3.small"
}

variable "github_repo" {
  description = "GitHub repository in owner/name form."
  type        = string
  default     = "bumblebee26/paper-trader"
}

variable "github_branch" {
  description = "Branch the server checks out and pulls every weekday at 08:45."
  type        = string
  default     = "main"
}

variable "telegram_bot_token" {
  description = "Token from @BotFather. Stored encrypted in SSM Parameter Store."
  type        = string
  sensitive   = true
}

variable "telegram_user_id" {
  description = "Your numeric Telegram user ID (also used as the chat ID)."
  type        = string
  default     = "8674025639"
}

variable "anthropic_api_key" {
  description = "Anthropic API key for Claude Code headless. Stored encrypted in SSM Parameter Store."
  type        = string
  sensitive   = true
}

variable "claude_model" {
  description = "Model alias Claude Code uses for the trading sessions."
  type        = string
  default     = "sonnet"
}

variable "max_budget_usd" {
  description = "Spending cap per Claude session (three sessions per weekday)."
  type        = number
  default     = 2
}

variable "enable_schedule" {
  description = "Start the server at 08:30 and stop it at 16:15 IST on weekdays to save about 65% of EC2 cost."
  type        = bool
  default     = true
}

variable "alert_email" {
  description = "Email for the monthly AWS budget alert. Leave empty to skip the budget."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Monthly AWS spend (EC2 etc., not Claude API) that triggers an email alert."
  type        = number
  default     = 20
}
