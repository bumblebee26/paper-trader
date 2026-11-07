output "instance_id" {
  description = "EC2 instance ID."
  value       = aws_instance.trader.id
}

output "deploy_key_public" {
  description = "Paste this into GitHub: repo Settings -> Deploy keys -> Add deploy key (read-only)."
  value       = tls_private_key.deploy.public_key_openssh
}

output "add_deploy_key_url" {
  description = "Where to add the deploy key."
  value       = "https://github.com/${var.github_repo}/settings/keys/new"
}

output "connect" {
  description = "Open a shell on the server (needs the AWS CLI + Session Manager plugin), or use EC2 -> Connect -> Session Manager in the console."
  value       = "aws ssm start-session --region ${var.aws_region} --target ${aws_instance.trader.id}"
}

output "bootstrap_log" {
  description = "Run this on the server to watch setup progress."
  value       = "sudo tail -f /var/log/paper-trader-bootstrap.log"
}
