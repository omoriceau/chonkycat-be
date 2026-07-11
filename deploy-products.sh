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
# Usage: $0 [--environment dev|staging|prod] [--region REGION] [--dev-email EMAIL] [--cors]
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
  --cors                                    Enable permissive CORS (Access-Control-
                                             Allow-Origin: *). Only allowed with
                                             --environment dev.
  -h, --help                                Show this help and exit

Examples:
  $0
  $0 --environment staging --region eu-west-1
  $0 --env dev --cors
  $0 --environment=prod --region=us-east-1 --dev-email=alerts@chonkychonk.com
EOF
}

ENVIRONMENT="dev"
REGION_ARG=""
DEV_EMAIL="dev@example.com"
ALLOW_CORS=true

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
    --cors)
      ALLOW_CORS=true
      shift
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

# --cors is deliberately restricted to dev. Permissive CORS (Access-Control-
# Allow-Origin: *) on a staging/prod API is an easy way to accidentally let
# any website read authenticated responses cross-origin — if you need CORS
# open in staging/prod for a real reason, do it explicitly in the template
# for that environment rather than via this shortcut flag.
if [ "$ALLOW_CORS" = true ] && [ "$ENVIRONMENT" != "dev" ]; then
    die "--cors is only allowed with --environment dev (got '$ENVIRONMENT'). Refusing to deploy permissive CORS to staging/prod."
fi

log "Deploying products lambda to $ENVIRONMENT in $REGION"
if [ "$ALLOW_CORS" = true ]; then
    warn "Permissive CORS enabled (--cors): Access-Control-Allow-Origin will be '*' for this deploy."
fi

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
# NOTE: this list is now DynamoDB table names + Stripe/dev-email config
# only. No DBHost/DBPort/DBUser/DBName/VpcId/SubnetIds/SecurityGroupId/
# DBPasswordSecretName — those are gone along with RDS. If template.yaml
# still lists any of them as Parameters, `sam deploy` will fail demanding a
# value; go drop them from the template rather than adding them back here.
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
)

# --cors: passed through as a parameter override. This assumes
# template.yaml declares an AllowCorsOrigin parameter and wires it into the
# API Gateway Cors config / response headers — if it doesn't yet, this
# override is a no-op and the template needs that parameter added.
if [ "$ALLOW_CORS" = true ]; then
  PARAM_OVERRIDES+=("ParameterKey=AllowCorsOrigin,ParameterValue=*")
fi

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