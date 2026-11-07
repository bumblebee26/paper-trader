# ------------------------------------------------------- server's own role --
data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "trader" {
  name               = "${local.name}-ec2"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# Lets you open a shell from the AWS console (Session Manager) with no SSH port.
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.trader.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Read only this project's parameters.
data "aws_iam_policy_document" "read_params" {
  statement {
    actions = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = [
      "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.param_prefix}/*",
      "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.param_prefix}",
    ]
  }
}

resource "aws_iam_role_policy" "read_params" {
  name   = "read-paper-trader-params"
  role   = aws_iam_role.trader.id
  policy = data.aws_iam_policy_document.read_params.json
}

resource "aws_iam_instance_profile" "trader" {
  name = "${local.name}-ec2"
  role = aws_iam_role.trader.name
}
