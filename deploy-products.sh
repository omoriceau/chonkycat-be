#!/usr/bin/env bash
set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ==============================================================================
# Argument parsing
#
# Usage: $0 [--environment dev|staging|prod] [--region REGION] [--dev-email EMAIL] [--ses-domain DOMAIN]
#
# All flags are optional and order-independent. Both "--flag value" and
# "--flag=value" forms are accepted. Run with --help for details.
# ==============================================================================

usage() {
  cat <<EOF
Usage: $0 [OPTIONS]

Options:
  --environment, --env <dev|staging|prod>   Target environment (default: dev)
  --region <region>                         AWS region (default: your AWS CLI's
                                             configured default region, falling
                                             back to us-east-1 with a warning)
  --dev-email <email>                       DevEmail parameter (default: dev@example.com)
  --ses-domain <domain>                     Domain to set up in SES for sending
                                             notification emails (creates/verifies
                                             the domain identity + DKIM, plus
                                             no-reply@<domain> and test@<domain>
                                             email identities). Can also be set
                                             via the SES_DOMAIN env var. Omit to
                                             skip SES setup entirely.
  --admin-cognito-pool-id <id>               Cognito pool the Users Lambda uses
                                             for AdminCreateUser (staff/admin
                                             accounts). Also settable via
                                             ADMIN_COGNITO_USER_POOL_ID.
                                             Default: chonkychonk-admin's pool.
  --customer-cognito-pool-id <id>            Cognito pool backing the storefront's
                                             Amplify Authenticator (chonky-cat-fe)
                                             — verifies cart/profile bearer
                                             tokens and authorizes POST
                                             /cart/claim + self-service profile
                                             routes. Also settable via
                                             CUSTOMER_COGNITO_USER_POOL_ID.
  --customer-cognito-client-id <id>          App client id for the pool above.
                                             Also settable via
                                             CUSTOMER_COGNITO_APP_CLIENT_ID.
  -h, --help                                Show this help and exit

Environment variables (SES/Cloudflare):
  SES_DOMAIN                Same as --ses-domain, used if the flag isn't passed.
  CLOUDFLARE_API_TOKEN      Optional. If set together with CLOUDFLARE_ZONE_ID,
                             the DKIM CNAME records SES needs are created/updated
                             directly in Cloudflare. Requires 'jq'.
  CLOUDFLARE_ZONE_ID        Optional, see above.

Environment variables (Cognito):
  ADMIN_COGNITO_USER_POOL_ID       Same as --admin-cognito-pool-id.
  CUSTOMER_COGNITO_USER_POOL_ID    Same as --customer-cognito-pool-id.
  CUSTOMER_COGNITO_APP_CLIENT_ID   Same as --customer-cognito-client-id.

Examples:
  $0
  $0 --environment staging --region eu-west-1
  $0 --environment=prod --region=us-east-1 --dev-email=alerts@chonkychonk.com
  $0 --ses-domain chonkycat.ca
  CLOUDFLARE_API_TOKEN=xxx CLOUDFLARE_ZONE_ID=yyy $0 --ses-domain chonkycat.ca
  $0 --customer-cognito-pool-id us-east-1_RN8iM0OaC --customer-cognito-client-id 607ej6ubfsn7o6q131f60f4kfc
EOF
}

ENVIRONMENT="dev"
REGION_ARG=""
DEV_EMAIL="dev@example.com"
SES_DOMAIN="${SES_DOMAIN:-}"
# Defaults match this account's actual pools (see `aws cognito-idp
# list-user-pools`) — chonkychonk-admin is a long-lived, rarely-changing
# resource so it's safe to default; the customer pool is Amplify-managed
# and its id/client id can change if that backend is ever torn down and
# re-sandboxed, so double-check these against `amplify_outputs.json` in
# chonky-cat-fe if profile/cart auth starts failing after an Amplify redeploy.
ADMIN_COGNITO_USER_POOL_ID="${ADMIN_COGNITO_USER_POOL_ID:-us-east-1_tzozLyJBF}"
CUSTOMER_COGNITO_USER_POOL_ID="${CUSTOMER_COGNITO_USER_POOL_ID:-us-east-1_RN8iM0OaC}"
CUSTOMER_COGNITO_APP_CLIENT_ID="${CUSTOMER_COGNITO_APP_CLIENT_ID:-607ej6ubfsn7o6q131f60f4kfc}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --environment=*|--env=*)
      ENVIRONMENT="${1#*=}"
      shift
      ;;
    --environment|--env)
      [ $# -ge 2 ] || die "$1 requires a value."
      ENVIRONMENT="$2"
      shift 2
      ;;
    --region=*)
      REGION_ARG="${1#*=}"
      shift
      ;;
    --region)
      [ $# -ge 2 ] || die "$1 requires a value."
      REGION_ARG="$2"
      shift 2
      ;;
    --dev-email=*)
      DEV_EMAIL="${1#*=}"
      shift
      ;;
    --dev-email)
      [ $# -ge 2 ] || die "$1 requires a value."
      DEV_EMAIL="$2"
      shift 2
      ;;
    --ses-domain=*)
      SES_DOMAIN="${1#*=}"
      shift
      ;;
    --ses-domain)
      [ $# -ge 2 ] || die "$1 requires a value."
      SES_DOMAIN="$2"
      shift 2
      ;;
    --admin-cognito-pool-id=*)
      ADMIN_COGNITO_USER_POOL_ID="${1#*=}"
      shift
      ;;
    --admin-cognito-pool-id)
      [ $# -ge 2 ] || die "$1 requires a value."
      ADMIN_COGNITO_USER_POOL_ID="$2"
      shift 2
      ;;
    --customer-cognito-pool-id=*)
      CUSTOMER_COGNITO_USER_POOL_ID="${1#*=}"
      shift
      ;;
    --customer-cognito-pool-id)
      [ $# -ge 2 ] || die "$1 requires a value."
      CUSTOMER_COGNITO_USER_POOL_ID="$2"
      shift 2
      ;;
    --customer-cognito-client-id=*)
      CUSTOMER_COGNITO_APP_CLIENT_ID="${1#*=}"
      shift
      ;;
    --customer-cognito-client-id)
      [ $# -ge 2 ] || die "$1 requires a value."
      CUSTOMER_COGNITO_APP_CLIENT_ID="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: '$1'. Run '$0 --help' for usage."
      ;;
  esac
done

# ==============================================================================
# Configuration
# ==============================================================================

# REGION: resolved from your AWS CLI's configured default region if you
# don't pass one explicitly, since that's more likely to match wherever
# terraform actually created the DynamoDB tables than a hardcoded guess.
# Still double-check this against the region you applied the terraform in —
# if `aws configure get region` returns nothing (no default set) this falls
# back to us-east-1 and warns you.
if [ -n "$REGION_ARG" ]; then
  REGION="$REGION_ARG"
else
  REGION="$(aws configure get region || true)"
  if [ -z "$REGION" ]; then
    REGION="us-east-1"
    warn "No region passed and no AWS CLI default region configured — falling back to us-east-1."
    warn "Pass it explicitly if your DynamoDB tables live elsewhere: $0 --environment $ENVIRONMENT --region <region>"
  fi
fi

case "$ENVIRONMENT" in
  dev|staging|prod)
    ;;
  *)
    die "Invalid environment: '$ENVIRONMENT'. Must be dev, staging, or prod."
    ;;
esac

# If Cloudflare auto-DNS was requested, make sure we can actually do it
# (needs jq to parse the API responses) before setup_ses gets there.
CF_AUTO_DNS=false
if [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && [ -n "${CLOUDFLARE_ZONE_ID:-}" ]; then
  if command -v jq >/dev/null 2>&1; then
    CF_AUTO_DNS=true
  else
    warn "CLOUDFLARE_API_TOKEN/CLOUDFLARE_ZONE_ID are set but 'jq' isn't installed — falling back to printing DNS records for manual entry."
  fi
fi

log "Deploying products lambda to $ENVIRONMENT in $REGION"
# CORS is resolved at request time from the deployed ENVIRONMENT (see
# shared/cors.py): "dev" echoes back any Origin, everything else only
# allows https://*.chonkycat.ca — nothing to configure here.

# ==============================================================================
# DynamoDB tables — all six lambdas are now fully migrated off RDS, so these
# are the only data-layer inputs this deploy needs.
#
# NAME_PREFIX must match `var.name_prefix` in the terraform that created
# these tables (aws_dynamodb_table.*.name = "${name_prefix}-<table>-${env}").
# Confirmed via `aws dynamodb list-tables` that the actual prefix is
# "chonky" — NOT "chonkychonk" like the rest of this stack's resource names
# (event bus, S3 bucket, Lambda function names, stack name all use
# "chonkychonk"). Two different naming conventions in play here; if
# terraform's name_prefix ever changes, update this to match.
# ==============================================================================

NAME_PREFIX="chonky"

USERS_TABLE_NAME="${NAME_PREFIX}-users-${ENVIRONMENT}"
PRODUCTS_TABLE_NAME="${NAME_PREFIX}-products-${ENVIRONMENT}"
ORDERS_TABLE_NAME="${NAME_PREFIX}-orders-${ENVIRONMENT}"
PAYMENTS_TABLE_NAME="${NAME_PREFIX}-payments-${ENVIRONMENT}"
PROMOTIONS_TABLE_NAME="${NAME_PREFIX}-promotions-${ENVIRONMENT}"

log "Verifying DynamoDB tables exist in $REGION..."
for TABLE in \
    "$USERS_TABLE_NAME" \
    "$PRODUCTS_TABLE_NAME" \
    "$ORDERS_TABLE_NAME" \
    "$PAYMENTS_TABLE_NAME" \
    "$PROMOTIONS_TABLE_NAME"
do
    if ! aws dynamodb describe-table --table-name "$TABLE" --region "$REGION" >/dev/null 2>&1; then
        die "DynamoDB table '$TABLE' not found in $REGION. Has the terraform for this environment been applied? Does NAME_PREFIX ('$NAME_PREFIX') match your terraform's name_prefix variable?"
    fi
    log "  found: $TABLE"
done

# ==============================================================================
# RDS / VPC — REMOVED.
#
# All six lambdas (users, products, orders, payments_api, stripe_webhook,
# email_service) are migrated to DynamoDB, so there is no longer any Lambda
# in this stack that needs VpcConfig or DB_HOST/DB_USER/DB_NAME/DB_PORT/
# DBPasswordSecretName/VpcId/SubnetIds/SecurityGroupId. This script no
# longer fetches an RDS endpoint, VPC, subnets, or security group, and no
# longer passes any of those as deploy parameters.
#
# This assumes template.yaml has also dropped VpcConfig and the RDS/VPC
# Parameters from every function. If template.yaml still declares any of
# them as required (no default), `sam deploy` below will fail asking for
# values this script no longer provides — that's your signal to go finish
# editing the template, not to re-add this block.
#
# Once you've confirmed the RDS instance, its security group, and the VPC
# (if nothing else in the account still uses them) are no longer needed,
# they can be decommissioned in AWS directly — this script never created
# them, so it can't clean them up for you.
# ==============================================================================

# S3 bucket for SAM artifacts
S3_BUCKET="chonkychonk-sam-artifacts-${ENVIRONMENT}"

log "Checking S3 bucket: $S3_BUCKET"
if ! aws s3api head-bucket --bucket "$S3_BUCKET" --region "$REGION" 2>/dev/null; then
    log "Creating S3 bucket: $S3_BUCKET"
    aws s3api create-bucket \
        --bucket "$S3_BUCKET" \
        --region "$REGION" \
        $(if [ "$REGION" != "us-east-1" ]; then echo "--create-bucket-configuration LocationConstraint=$REGION"; fi) \
        2>/dev/null || warn "Bucket may already exist, continuing..."
fi

# EventBridge bus — shared by orders, users, and any future services.
# Lambdas fall back to this name via the EVENT_BUS_NAME env var default.
EVENT_BUS_NAME="chonkychonk-bus"

log "Checking EventBridge event bus: $EVENT_BUS_NAME"
if ! aws events describe-event-bus --name "$EVENT_BUS_NAME" --region "$REGION" >/dev/null 2>&1; then
    log "Creating EventBridge event bus: $EVENT_BUS_NAME"
    aws events create-event-bus \
        --name "$EVENT_BUS_NAME" \
        --region "$REGION"
else
    log "Event bus $EVENT_BUS_NAME already exists"
fi

# ==============================================================================
# SES: verify a sending domain (+ Easy DKIM) and create no-reply@ / test@
# email identities under it.
#
# Uses SESv2 (`aws sesv2 ...`), not the older `aws ses verify-*` v1 calls —
# v1's verify-domain-identity/verify-email-identity reset verification
# state and resend the verification email on every single call, which
# would make this non-idempotent (re-running the deploy would keep
# knocking already-verified identities back to pending, or spamming
# no-reply@/test@ with fresh verification emails). SESv2's
# create-email-identity is safe to call once per identity; this script
# additionally checks with get-email-identity first so a second run is a
# no-op rather than an error.
#
# Domain is assumed to be hosted on Cloudflare, not Route53, so this
# script can't create the DKIM CNAME records for you unless you export
# CLOUDFLARE_API_TOKEN + CLOUDFLARE_ZONE_ID (needs `jq`, checked above) —
# otherwise it just prints the records for you to add by hand.
# ==============================================================================

cf_upsert_cname() {
  local name="$1" content="$2"
  local existing_id
  existing_id=$(curl -sf -X GET \
      "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records?type=CNAME&name=${name}" \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      -H "Content-Type: application/json" \
    | jq -r '.result[0].id // empty') || { warn "  Cloudflare lookup failed for $name — skipping, add it manually."; return; }

  if [ -n "$existing_id" ]; then
    curl -sf -X PUT "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records/${existing_id}" \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      -H "Content-Type: application/json" \
      --data "{\"type\":\"CNAME\",\"name\":\"${name}\",\"content\":\"${content}\",\"ttl\":300,\"proxied\":false}" >/dev/null \
      && log "  updated CNAME: $name -> $content" \
      || warn "  failed to update CNAME $name — add it manually."
  else
    curl -sf -X POST "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records" \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      -H "Content-Type: application/json" \
      --data "{\"type\":\"CNAME\",\"name\":\"${name}\",\"content\":\"${content}\",\"ttl\":300,\"proxied\":false}" >/dev/null \
      && log "  created CNAME: $name -> $content" \
      || warn "  failed to create CNAME $name — add it manually."
  fi
}

setup_ses() {
  if [ -z "$SES_DOMAIN" ]; then
    warn "No SES domain given — skipping SES setup. Pass --ses-domain <domain> or export SES_DOMAIN=<domain> to enable."
    return
  fi

  log "Setting up SES for domain: $SES_DOMAIN"

  # ---- Domain identity (enables Easy DKIM by default) ----
  if aws sesv2 get-email-identity --email-identity "$SES_DOMAIN" --region "$REGION" >/dev/null 2>&1; then
    log "SES domain identity $SES_DOMAIN already exists — skipping creation."
  else
    log "Creating SES domain identity: $SES_DOMAIN"
    aws sesv2 create-email-identity \
        --email-identity "$SES_DOMAIN" \
        --region "$REGION" >/dev/null
  fi

  local verified dkim_tokens
  verified=$(aws sesv2 get-email-identity --email-identity "$SES_DOMAIN" --region "$REGION" \
      --query 'VerifiedForSendingStatus' --output text)
  dkim_tokens=$(aws sesv2 get-email-identity --email-identity "$SES_DOMAIN" --region "$REGION" \
      --query 'DkimAttributes.Tokens' --output text)

  if [ "$verified" = "True" ]; then
    log "$SES_DOMAIN is verified for sending."
  else
    warn "$SES_DOMAIN is not yet verified. Add these CNAME records in Cloudflare DNS (DNS-only / grey cloud, NOT proxied):"
    for token in $dkim_tokens; do
      echo "    ${token}._domainkey.${SES_DOMAIN}  CNAME  ${token}.dkim.amazonses.com"
    done
    echo "  Verification usually completes within a few minutes to ~72h of the records propagating."

    if [ "$CF_AUTO_DNS" = true ]; then
      log "Upserting the DKIM CNAME records above directly in Cloudflare..."
      for token in $dkim_tokens; do
        cf_upsert_cname "${token}._domainkey.${SES_DOMAIN}" "${token}.dkim.amazonses.com"
      done
    else
      warn "Export CLOUDFLARE_API_TOKEN and CLOUDFLARE_ZONE_ID (and have 'jq' installed) to have this script create those records for you automatically next time."
    fi
  fi

  # ---------------------------------------------------------------------
  # no-reply@ / test@ identities.
  #
  # Not strictly required once the domain identity above is verified —
  # SES will send FROM and accept mail TO any address at a verified
  # domain. Created explicitly anyway (as requested) so SES shows
  # per-address identities/metrics for the two addresses this stack
  # actually uses: no-reply@ as EmailServiceFunction's sender, test@ for
  # sandbox-mode test recipients. Each still needs its own click-through
  # verification email — that part is inherently not automatable.
  # ---------------------------------------------------------------------
  for local_part in no-reply test; do
    local identity="${local_part}@${SES_DOMAIN}"
    if aws sesv2 get-email-identity --email-identity "$identity" --region "$REGION" >/dev/null 2>&1; then
      log "SES email identity $identity already exists — skipping creation."
    else
      log "Creating SES email identity: $identity"
      aws sesv2 create-email-identity --email-identity "$identity" --region "$REGION" >/dev/null
      warn "  Verification email sent to $identity — someone with access to that mailbox needs to click the link before it can send/receive."
    fi
  done

  log "SES setup done for $SES_DOMAIN."
}

setup_ses

# ==============================================================================
# Build
# ==============================================================================

log "Building lambda package..."
sam build --config-env dev

# ==============================================================================
# Deploy
# ==============================================================================

STACK_NAME="chonkychonk-products-${ENVIRONMENT}"

log "Deploying stack: $STACK_NAME"

# Check if stack exists and is in a failed state
STACK_STATUS=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query 'Stacks[0].StackStatus' \
    --output text 2>/dev/null || echo "")

if [[ "$STACK_STATUS" == "UPDATE_ROLLBACK_FAILED" ]]; then
    log "Stack is in UPDATE_ROLLBACK_FAILED state, deleting it..."
    aws cloudformation delete-stack \
        --stack-name "$STACK_NAME" \
        --region "$REGION"
    
    log "Waiting for stack deletion to complete..."
    aws cloudformation wait stack-delete-complete \
        --stack-name "$STACK_NAME" \
        --region "$REGION" 2>/dev/null || warn "Stack deletion check timed out, proceeding anyway..."
    
    log "Stack deleted successfully"
fi

# Build parameter overrides for all required parameters
#
# NOTE: this list is now DynamoDB table names + Stripe/dev-email/SES/Cognito
# config only. No DBHost/DBPort/DBUser/DBName/VpcId/SubnetIds/SecurityGroupId/
# DBPasswordSecretName — those are gone along with RDS. If template.yaml
# still lists any of them as Parameters, `sam deploy` will fail demanding a
# value; go drop them from the template rather than adding them back here.
#
# This is the actual, effective source of these parameters' deployed
# values — samconfig.toml's own parameter_overrides only apply to a bare
# `sam deploy` with no --parameter-overrides flag, which this script never
# does (it always passes its own list below, which wins). If a parameter
# looks right in samconfig.toml but wrong on the deployed stack, this is
# almost certainly why — keep both in sync manually.
PARAM_OVERRIDES=(
  "ParameterKey=Environment,ParameterValue=$ENVIRONMENT"
  "ParameterKey=UsersTableName,ParameterValue=$USERS_TABLE_NAME"
  "ParameterKey=ProductsTableName,ParameterValue=$PRODUCTS_TABLE_NAME"
  "ParameterKey=OrdersTableName,ParameterValue=$ORDERS_TABLE_NAME"
  "ParameterKey=PaymentsTableName,ParameterValue=$PAYMENTS_TABLE_NAME"
  "ParameterKey=PromotionsTableName,ParameterValue=$PROMOTIONS_TABLE_NAME"
  "ParameterKey=EventBusName,ParameterValue=$EVENT_BUS_NAME"
  "ParameterKey=StripeSecretKeySecretName,ParameterValue=chonky/${ENVIRONMENT}/stripe_secret_key"
  "ParameterKey=StripeWebhookSecretName,ParameterValue=chonky/${ENVIRONMENT}/stripe_webhook_secret"
  "ParameterKey=DevEmail,ParameterValue=$DEV_EMAIL"
  "ParameterKey=SesDomain,ParameterValue=$SES_DOMAIN"
  "ParameterKey=CognitoUserPoolId,ParameterValue=$ADMIN_COGNITO_USER_POOL_ID"
  "ParameterKey=CustomerCognitoUserPoolId,ParameterValue=$CUSTOMER_COGNITO_USER_POOL_ID"
  "ParameterKey=CustomerCognitoAppClientId,ParameterValue=$CUSTOMER_COGNITO_APP_CLIENT_ID"
)

sam deploy \
    --template-file .aws-sam/build/template.yaml \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --s3-bucket "$S3_BUCKET" \
    --capabilities CAPABILITY_IAM \
    --no-confirm-changeset \
    --parameter-overrides "${PARAM_OVERRIDES[@]}"

# ==============================================================================
# Custom domain: map this stack's API to api.chonkycat.ca
#
# The domain itself (and its ACM cert) is provisioned out-of-band in API
# Gateway already — this only creates/updates the base path mapping so
# api.chonkycat.ca routes to this stack's REST API + live stage.
#
# The live stage name is looked up from API Gateway directly rather than
# assumed to equal $ENVIRONMENT: when ServerlessRestApi.StageName is set to
# !Ref Environment, SAM's transform can't resolve that intrinsic at macro
# time and silently falls back to hardcoding the stage name "Prod"
# regardless of the parameter's actual value (confirmed against this
# account's deployed stack). Filtering get-stages by the
# aws:cloudformation:stack-name tag finds whichever stage this stack
# actually owns, bug or no bug.
#
# Base path "" maps the domain root straight to this one stage. That only
# works cleanly for a single live environment sharing the domain — if
# staging/prod ever run side by side, each additional one needs its own
# base path (e.g. /staging) since only one stack can hold the root mapping.
# ==============================================================================
attach_custom_domain() {
  local domain_name="api.chonkycat.ca"

  log "Attaching custom domain: $domain_name"

  if ! aws apigateway get-domain-names \
      --region "$REGION" \
      --query "items[?domainName=='$domain_name']" \
      --output text 2>/dev/null | grep -q .; then
    warn "Custom domain '$domain_name' not found in API Gateway — skipping mapping. Provision it (with its ACM cert) first."
    return
  fi

  local api_id
  api_id=$(aws cloudformation describe-stack-resources \
      --stack-name "$STACK_NAME" \
      --logical-resource-id ServerlessRestApi \
      --region "$REGION" \
      --query 'StackResources[0].PhysicalResourceId' \
      --output text 2>/dev/null || echo "")

  if [ -z "$api_id" ] || [ "$api_id" = "None" ]; then
    warn "Could not find ServerlessRestApi in stack $STACK_NAME — skipping custom domain mapping."
    return
  fi

  local stage_name
  stage_name=$(aws apigateway get-stages \
      --rest-api-id "$api_id" \
      --region "$REGION" \
      --query "item[?tags.\"aws:cloudformation:stack-name\"=='${STACK_NAME}'].stageName | [0]" \
      --output text 2>/dev/null || echo "")

  if [ -z "$stage_name" ] || [ "$stage_name" = "None" ]; then
    warn "Could not determine the live API Gateway stage for $STACK_NAME — skipping custom domain mapping."
    return
  fi

  log "Mapping $domain_name -> API $api_id, stage $stage_name"

  if aws apigateway get-base-path-mapping \
      --domain-name "$domain_name" \
      --base-path "(none)" \
      --region "$REGION" >/dev/null 2>&1; then
    log "Base path mapping already exists — updating it to point at $api_id/$stage_name"
    aws apigateway update-base-path-mapping \
        --domain-name "$domain_name" \
        --base-path "(none)" \
        --patch-operations op=replace,path=/restapiId,value="$api_id" op=replace,path=/stage,value="$stage_name" \
        --region "$REGION" >/dev/null
  else
    log "Creating base path mapping"
    aws apigateway create-base-path-mapping \
        --domain-name "$domain_name" \
        --rest-api-id "$api_id" \
        --stage "$stage_name" \
        --base-path "" \
        --region "$REGION" >/dev/null
  fi

  log "Custom domain mapping configured: https://$domain_name -> $api_id ($stage_name)"
}

attach_custom_domain

# ==============================================================================
# EventBridge: wire UserCreated events (from the users Lambda) to the
# Email Lambda so new users get a welcome email.
#
# NOTE: this rule/target/permission is created imperatively via the CLI,
# not through the SAM template — it will NOT show up in `sam deploy` diffs
# and will NOT be cleaned up if the stack is deleted. If the users Lambda
# ever moves into this same SAM template, move this block into the
# template's Events property instead so CloudFormation tracks it.
# ==============================================================================

log "Wiring UserCreated -> Email Lambda EventBridge rule..."

ACCOUNT_ID=$(aws sts get-caller-identity --query 'Account' --output text)

# Assumes the email Lambda's logical resource ID in the SAM template is
# "EmailServiceFunction" (matches the existing OrderCreated/etc. rule name).
# Adjust --logical-resource-id below if yours differs.
EMAIL_LAMBDA_NAME=$(aws cloudformation describe-stack-resources \
    --stack-name "$STACK_NAME" \
    --logical-resource-id EmailServiceFunction \
    --region "$REGION" \
    --query 'StackResources[0].PhysicalResourceId' \
    --output text 2>/dev/null || echo "")

if [ -z "$EMAIL_LAMBDA_NAME" ] || [ "$EMAIL_LAMBDA_NAME" = "None" ]; then
    warn "Could not find EmailServiceFunction in stack $STACK_NAME — skipping UserCreated rule setup."
    warn "Wire it up manually once you confirm the correct logical/physical resource name."
else
    EMAIL_LAMBDA_ARN=$(aws lambda get-function \
        --function-name "$EMAIL_LAMBDA_NAME" \
        --region "$REGION" \
        --query 'Configuration.FunctionArn' \
        --output text)

    USERS_RULE_NAME="chonkychonk-users-${ENVIRONMENT}-EmailServiceFunctionUserCreated"

    log "Creating/updating rule: $USERS_RULE_NAME"
    aws events put-rule \
        --name "$USERS_RULE_NAME" \
        --event-bus-name "$EVENT_BUS_NAME" \
        --event-pattern '{"source":["chonkychonk.users"],"detail-type":["UserCreated"]}' \
        --region "$REGION" >/dev/null

    log "Pointing rule at Email Lambda: $EMAIL_LAMBDA_ARN"
    aws events put-targets \
        --rule "$USERS_RULE_NAME" \
        --event-bus-name "$EVENT_BUS_NAME" \
        --region "$REGION" \
        --targets "[{\"Id\":\"EmailServiceFunction\",\"Arn\":\"$EMAIL_LAMBDA_ARN\"}]" >/dev/null

    log "Granting EventBridge permission to invoke Email Lambda"
    aws lambda add-permission \
        --function-name "$EMAIL_LAMBDA_NAME" \
        --statement-id "AllowEventBridgeUsersInvoke" \
        --action lambda:InvokeFunction \
        --principal events.amazonaws.com \
        --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${EVENT_BUS_NAME}/${USERS_RULE_NAME}" \
        --region "$REGION" 2>/dev/null || warn "Permission statement may already exist, continuing..."

    log "UserCreated -> Email Lambda rule configured: $USERS_RULE_NAME"
fi

# ==============================================================================
# Output
# ==============================================================================

log "Deployment complete!"
log ""

STACK_INFO=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs' \
    --output table)

echo "$STACK_INFO"

log ""
log "Stack status:"
aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query 'Stacks[0].StackStatus' \
    --output text