#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# One-time setup: lets GitHub Actions deploy this stack via short-lived OIDC
# credentials instead of long-lived AWS access keys.
#
# Creates (idempotently — safe to re-run):
#   1. An IAM OIDC provider trusting token.actions.githubusercontent.com
#      (account-wide; only created once even if other repos add roles later)
#   2. An IAM role that only the matching GitHub Actions *environment*
#      (dev or prod) can assume, scoped via the OIDC `sub` claim as
#      `repo:<repo>:environment:<name>` — NOT a branch ref. GitHub swaps
#      the token's sub claim to this environment form for any job that
#      sets `environment:` in the workflow, which deploy-dev/deploy-prod
#      both do; a ref-based condition (`ref:refs/heads/master`) silently
#      never matches those tokens and every AssumeRoleWithWebIdentity call
#      fails with "Not authorized" — that's the bug this script used to
#      have. PR-validation runs don't set `environment:` and never call
#      configure-aws-credentials, so they don't need either role at all.
#   3. Permissions on that role covering what deploy-products.sh + `sam
#      deploy` touch: CloudFormation, Lambda, API Gateway, EventBridge (all
#      via a single customer-managed policy, chonky-cat-be-deploy-services-
#      scoped, shared by both the dev and prod roles and scoped to this
#      app's own resources rather than the broad AWS-managed FullAccess
#      policies this used to attach), plus IAM (CreateRole/PassRole/
#      Put*Policy — required for SAM's CAPABILITY_IAM), the SAM artifacts S3
#      bucket, and DynamoDB DescribeTable (the script only verifies tables
#      exist) via a separate inline policy. The CloudFormation statement
#      also needs an explicit grant on the AWS::Serverless-2016-10-31
#      transform's own pseudo-resource ARN (arn:...:aws:transform/...) —
#      distinct from the stack ARN — or CreateChangeSet fails outright on
#      any template using `Transform:`.
#
# Usage:
#   ./setup-github-actions-oidc.sh              # creates/updates the dev role
#   ./setup-github-actions-oidc.sh --environment prod   # creates/updates the prod role
#
# Each role trusts only its matching GitHub Environment name (dev's role
# trusts environment:dev, prod's trusts environment:prod) — this is also
# what makes the roles/secrets distinct from each other, on top of the
# 'prod' Environment's required-reviewer gate.
#
# After running, set the printed ARN as a repo secret — AWS_DEPLOY_ROLE_ARN
# for dev, AWS_DEPLOY_ROLE_ARN_PROD (under the 'prod' Environment) for prod.
# See CICD.md.
# ==============================================================================

# ==============================================================================
# CONFIG
# ==============================================================================
ENVIRONMENT="dev"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --environment=*|--env=*) ENVIRONMENT="${1#*=}"; shift ;;
    --environment|--env)     ENVIRONMENT="$2"; shift 2 ;;
    *) echo "[ERROR] Unknown argument: $1" >&2; exit 1 ;;
  esac
done

case "$ENVIRONMENT" in
  dev)  ROLE_NAME="chonky-cat-be-github-actions-deploy" ;;
  prod) ROLE_NAME="chonky-cat-be-github-actions-deploy-prod" ;;
  *) echo "[ERROR] Invalid environment: '$ENVIRONMENT'. Must be dev or prod." >&2; exit 1 ;;
esac

GITHUB_REPO="omoriceau/chonkycat-be"
OIDC_HOST="token.actions.githubusercontent.com"

# Thumbprint of the top intermediate cert served by token.actions.githubusercontent.com
# (ISRG Root YR, issued by ISRG Root X1 — GitHub serves via Let's Encrypt, not the
# DigiCert chain older guides reference). AWS no longer actually validates this
# value against the live chain, but the API still requires one of the right shape.
# Re-derive with:
#   openssl s_client -connect token.actions.githubusercontent.com:443 -showcerts </dev/null 2>/dev/null \
#     | openssl x509 -noout -fingerprint -sha1 | sed 's/.*=//; s/://g' | tr 'A-F' 'a-f'
OIDC_THUMBPRINT="ab9d0263244dd0326eb67015705a667e79cfe998"

# ==============================================================================
# HELPERS
# ==============================================================================
log()  { echo "[INFO]  $*"; }
warn() { echo "[WARN]  $*"; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
OIDC_PROVIDER_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_HOST}"

# ==============================================================================
# 1. OIDC PROVIDER
# ==============================================================================
log "Checking for existing OIDC provider ($OIDC_HOST)..."

if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_PROVIDER_ARN" >/dev/null 2>&1; then
    log "OIDC provider already exists: $OIDC_PROVIDER_ARN — skipping creation."
else
    log "OIDC provider not found — creating..."
    aws iam create-open-id-connect-provider \
        --url "https://${OIDC_HOST}" \
        --client-id-list "sts.amazonaws.com" \
        --thumbprint-list "$OIDC_THUMBPRINT" >/dev/null
    log "Created OIDC provider: $OIDC_PROVIDER_ARN"
fi

# ==============================================================================
# 2. IAM ROLE — trust policy scoped to this environment only
# ==============================================================================
log "Checking for existing role '$ROLE_NAME' (environment: $ENVIRONMENT)..."

TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Federated": "${OIDC_PROVIDER_ARN}" },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": { "${OIDC_HOST}:aud": "sts.amazonaws.com" },
        "StringLike":   { "${OIDC_HOST}:sub": "repo:${GITHUB_REPO}:environment:${ENVIRONMENT}" }
      }
    }
  ]
}
EOF
)

if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
    log "Role already exists — updating trust policy to match current config..."
    aws iam update-assume-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-document "$TRUST_POLICY"
else
    log "Role not found — creating..."
    aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document "$TRUST_POLICY" \
        --description "Assumed by chonky-cat-be's deploy-${ENVIRONMENT} GitHub Actions job via OIDC" >/dev/null
    log "Created role: $ROLE_NAME"
fi

ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)

# ==============================================================================
# 3. PERMISSIONS
#
# attach-role-policy / put-role-policy are both idempotent — safe to run
# every time, no existence check needed.
# ==============================================================================
log "Ensuring shared least-privilege policy 'chonky-cat-be-deploy-services-scoped' is up to date..."

# One customer-managed policy shared by both the dev and prod roles (resource
# patterns below use ${AWS::AccountId}-style wildcards that match either
# environment's stack/function names, e.g. chonkychonk-products-dev and
# chonkychonk-products-production both match stack/chonkychonk-products-*/*)
# — this replaced the old AWSCloudFormationFullAccess / AWSLambda_FullAccess /
# AmazonAPIGatewayAdministrator / AmazonEventBridgeFullAccess managed-policy
# attachments with something scoped to just this app's resources.
SERVICES_POLICY_NAME="chonky-cat-be-deploy-services-scoped"
SERVICES_POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${SERVICES_POLICY_NAME}"

SERVICES_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudFormationStackDeploy",
      "Effect": "Allow",
      "Action": [
        "cloudformation:CreateStack",
        "cloudformation:UpdateStack",
        "cloudformation:DeleteStack",
        "cloudformation:DescribeStacks",
        "cloudformation:DescribeStackEvents",
        "cloudformation:DescribeStackResource",
        "cloudformation:DescribeStackResources",
        "cloudformation:GetTemplate",
        "cloudformation:GetTemplateSummary",
        "cloudformation:ListStackResources",
        "cloudformation:CreateChangeSet",
        "cloudformation:DescribeChangeSet",
        "cloudformation:ExecuteChangeSet",
        "cloudformation:DeleteChangeSet",
        "cloudformation:ListChangeSets",
        "cloudformation:TagResource",
        "cloudformation:ValidateTemplate"
      ],
      "Resource": "arn:aws:cloudformation:*:${ACCOUNT_ID}:stack/chonkychonk-products-*/*"
    },
    {
      "Sid": "SamTransform",
      "Effect": "Allow",
      "Action": "cloudformation:CreateChangeSet",
      "Resource": "arn:aws:cloudformation:*:aws:transform/Serverless-2016-10-31"
    },
    {
      "Sid": "LambdaFunctionDeploy",
      "Effect": "Allow",
      "Action": [
        "lambda:CreateFunction",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration",
        "lambda:GetFunction",
        "lambda:GetFunctionConfiguration",
        "lambda:DeleteFunction",
        "lambda:AddPermission",
        "lambda:RemovePermission",
        "lambda:GetPolicy",
        "lambda:PublishVersion",
        "lambda:ListVersionsByFunction",
        "lambda:CreateAlias",
        "lambda:UpdateAlias",
        "lambda:DeleteAlias",
        "lambda:GetAlias",
        "lambda:TagResource",
        "lambda:UntagResource",
        "lambda:ListTags",
        "lambda:PutFunctionConcurrency"
      ],
      "Resource": "arn:aws:lambda:*:${ACCOUNT_ID}:function:chonkychonk-*"
    },
    {
      "Sid": "LambdaLayerDeploy",
      "Effect": "Allow",
      "Action": [
        "lambda:PublishLayerVersion",
        "lambda:GetLayerVersion",
        "lambda:DeleteLayerVersion",
        "lambda:ListLayerVersions"
      ],
      "Resource": "arn:aws:lambda:*:${ACCOUNT_ID}:layer:chonkychonk-*"
    },
    {
      "Sid": "EventBridgeRulesAndBus",
      "Effect": "Allow",
      "Action": [
        "events:DescribeEventBus",
        "events:PutRule",
        "events:DescribeRule",
        "events:PutTargets",
        "events:RemoveTargets",
        "events:ListTargetsByRule",
        "events:DeleteRule",
        "events:TagResource"
      ],
      "Resource": [
        "arn:aws:events:*:${ACCOUNT_ID}:event-bus/chonkychonk-bus",
        "arn:aws:events:*:${ACCOUNT_ID}:rule/chonkychonk-bus/*"
      ]
    },
    {
      "Sid": "ApiGatewayManagement",
      "Effect": "Allow",
      "Action": [
        "apigateway:GET",
        "apigateway:POST",
        "apigateway:PUT",
        "apigateway:PATCH",
        "apigateway:DELETE"
      ],
      "Resource": [
        "arn:aws:apigateway:*::/restapis",
        "arn:aws:apigateway:*::/restapis/*",
        "arn:aws:apigateway:*::/tags/*"
      ]
    }
  ]
}
EOF
)

if aws iam get-policy --policy-arn "$SERVICES_POLICY_ARN" >/dev/null 2>&1; then
    # A managed policy can only hold 5 versions — drop the oldest non-default
    # one first if we're already at the cap, so create-policy-version doesn't
    # fail on a re-run.
    OLD_VERSIONS=$(aws iam list-policy-versions --policy-arn "$SERVICES_POLICY_ARN" \
        --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text)
    VERSION_COUNT=$(aws iam list-policy-versions --policy-arn "$SERVICES_POLICY_ARN" \
        --query 'length(Versions)' --output text)
    if [[ "$VERSION_COUNT" -ge 5 ]]; then
        OLDEST=$(aws iam list-policy-versions --policy-arn "$SERVICES_POLICY_ARN" \
            --query 'Versions[?IsDefaultVersion==`false`] | sort_by(@, &CreateDate)[0].VersionId' --output text)
        aws iam delete-policy-version --policy-arn "$SERVICES_POLICY_ARN" --version-id "$OLDEST"
        log "  pruned oldest policy version ($OLDEST) to stay under the 5-version cap"
    fi
    aws iam create-policy-version --policy-arn "$SERVICES_POLICY_ARN" \
        --policy-document "$SERVICES_POLICY" --set-as-default >/dev/null
    log "  updated existing policy: $SERVICES_POLICY_ARN"
else
    aws iam create-policy --policy-name "$SERVICES_POLICY_NAME" \
        --policy-document "$SERVICES_POLICY" >/dev/null
    log "  created policy: $SERVICES_POLICY_ARN"
fi

aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn "$SERVICES_POLICY_ARN"
log "  attached to $ROLE_NAME"

log "Putting scoped inline policy (IAM PassRole for Lambda exec roles, SAM artifacts bucket, DynamoDB describe, Lambda logs)..."

INLINE_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PassAndManageLambdaExecutionRoles",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:GetRole",
        "iam:PassRole",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:GetRolePolicy",
        "iam:TagRole",
        "iam:UntagRole"
      ],
      "Resource": "arn:aws:iam::${ACCOUNT_ID}:role/chonkychonk-*"
    },
    {
      "Sid": "SamArtifactsBucket",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket",
        "s3:CreateBucket",
        "s3:HeadBucket",
        "s3:PutBucketPolicy",
        "s3:PutEncryptionConfiguration",
        "s3:PutBucketVersioning"
      ],
      "Resource": [
        "arn:aws:s3:::chonkychonk-sam-artifacts-*",
        "arn:aws:s3:::chonkychonk-sam-artifacts-*/*"
      ]
    },
    {
      "Sid": "DynamoDbTableCheck",
      "Effect": "Allow",
      "Action": ["dynamodb:DescribeTable"],
      "Resource": "arn:aws:dynamodb:*:${ACCOUNT_ID}:table/chonky-*"
    },
    {
      "Sid": "LambdaLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:DeleteLogGroup",
        "logs:PutRetentionPolicy",
        "logs:DescribeLogGroups",
        "logs:TagResource"
      ],
      "Resource": "*"
    }
  ]
}
EOF
)

aws iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name "chonky-cat-be-deploy-scoped" \
    --policy-document "$INLINE_POLICY"

# ==============================================================================
# DONE
# ==============================================================================
log "====== DONE ======"
log "Role ARN: $ROLE_ARN"
log ""
if [[ "$ENVIRONMENT" == "prod" ]]; then
    log "Set this as the AWS_DEPLOY_ROLE_ARN_PROD secret on the 'prod' GitHub"
    log "Environment (repo Settings → Environments → prod → Secrets) so the"
    log "deploy-prod workflow job can use it:"
    log "  gh secret set AWS_DEPLOY_ROLE_ARN_PROD --repo ${GITHUB_REPO} --env prod --body \"${ROLE_ARN}\""
    log ""
    log "Also fill in samconfig.toml's [prod.deploy.parameters] section (table"
    log "names, Cognito pool IDs, SES domain, alert email) — ci-deploy.sh reads"
    log "prod config from there, not from GitHub secrets, and refuses to deploy"
    log "while any TODO-* placeholders remain. See CICD.md."
else
    log "Set this as a secret on the repo so the deploy-dev workflow job can use it:"
    log "  gh secret set AWS_DEPLOY_ROLE_ARN --repo ${GITHUB_REPO} --body \"${ROLE_ARN}\""
fi
