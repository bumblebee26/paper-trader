variable "aws_region" {
  description = "AWS region. The free plan (projects experience) allows only us-east-1, us-west-2 and the project's region; this project's region is Sydney."
  type        = string
  default     = "ap-southeast-2"
}

variable "name_prefix" {
  description = "Prefix for trader resources. Must start with the lakehouse prefix so the shared CI apply user (role/stocks-*) can manage them."
  type        = string
  default     = "stocks-trader"
}

variable "lakehouse_prefix" {
  description = "name_prefix of the stocks_lakehouse stacks (its SSM parameters and CI users)."
  type        = string
  default     = "stocks"
}

variable "lakehouse_landing_volume" {
  description = "Landing volume of the stocks_lakehouse; the daily journal export goes to its trades/ folder."
  type        = string
  default     = "/Volumes/stocks/bronze/landing"
}

variable "github_repo" {
  description = "Repository whose trading-session workflow the kicker starts (owner/name)."
  type        = string
  default     = "bumblebee26/paper-trader"
}

variable "lambda_python_runtime" {
  description = "Lambda runtime for the kicker and guardian."
  type        = string
  default     = "python3.13"
}

variable "log_retention_days" {
  description = "Days to keep Lambda logs in CloudWatch."
  type        = number
  default     = 14
}

variable "state_noncurrent_version_days" {
  description = "Days to keep overwritten versions of ledger and journal files."
  type        = number
  default     = 30
}

variable "dispatch_enabled" {
  description = "Kicker schedules: start the GitHub Actions sessions at exact IST times. Needs the GitHub token in SSM."
  type        = bool
  default     = false
}

variable "guardian_enabled" {
  description = "Guardian schedules: 15:12 IST square-off and 15:40 IST report backstops (no Claude needed)."
  type        = bool
  default     = false
}
