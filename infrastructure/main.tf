locals {
  name         = "paper-trader"
  param_prefix = "/paper-trader"
}

# ---------------------------------------------------------------- network ---
# Uses the region's default VPC. No inbound ports are opened: you connect with
# SSM Session Manager, and the server only makes outbound calls (GitHub,
# Telegram, Yahoo Finance, Anthropic, package mirrors).
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_security_group" "trader" {
  name        = "${local.name}-sg"
  description = "paper-trader: outbound only"
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ------------------------------------------------------------------- server --
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "trader" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = sort(data.aws_subnets.default.ids)[0]
  vpc_security_group_ids      = [aws_security_group.trader.id]
  iam_instance_profile        = aws_iam_instance_profile.trader.name
  associate_public_ip_address = true

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    region         = var.aws_region
    param_prefix   = local.param_prefix
    github_repo    = var.github_repo
    github_branch  = var.github_branch
    claude_model   = var.claude_model
    max_budget_usd = var.max_budget_usd
  })
  user_data_replace_on_change = false

  metadata_options {
    http_tokens   = "required" # IMDSv2 only
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 12
    encrypted   = true
  }

  tags = {
    Name = local.name
  }

  lifecycle {
    # A newer Ubuntu image must not replace a running server with its journal.
    ignore_changes = [ami]

    precondition {
      condition     = var.claude_oauth_token != "" || var.anthropic_api_key != ""
      error_message = "Set claude_oauth_token (Claude Pro/Max, from `claude setup-token`) or anthropic_api_key in terraform.tfvars."
    }
  }

  # Secrets must exist in SSM before first boot reads them.
  depends_on = [
    aws_ssm_parameter.telegram_bot_token,
    aws_ssm_parameter.telegram_user_id,
    aws_ssm_parameter.deploy_key_private,
    aws_ssm_parameter.anthropic_api_key,
    aws_ssm_parameter.claude_oauth_token,
  ]
}

# ------------------------------------------------- GitHub deploy key (read) --
# Terraform generates the key; you paste the public half into GitHub once
# (see the deploy_key_public output). The private half goes to SSM.
resource "tls_private_key" "deploy" {
  algorithm = "ED25519"
}
