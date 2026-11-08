# Shared, non-secret settings for the aws stack. Committed to git so local runs
# and CI/CD apply the same values. (*.auto.tfvars loads automatically and takes
# precedence over terraform.tfvars.) Put secrets in SSM or GitHub secrets, never here.

# 15:12 square-off and 15:40 report backstops (no Claude needed).
guardian_enabled = true

# Kicker: start sessions at exact IST times. Turn on after the GitHub token is in SSM.
dispatch_enabled = true
