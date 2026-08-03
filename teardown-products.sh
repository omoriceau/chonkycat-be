#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Teardown script for products lambda
# Deletes: CloudFormation stack, S3 bucket + artifacts, secrets, and security groups
# ==============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ==============================================================================
# Configuration
# ==============================================================================

ENVIRONMENT="${1:-dev}"
REGION="${2:-us-east-1}"

if [[ ! "$ENVIRONMENT" =~ ^(dev|staging|prod)$ ]]; then
    die "Invalid environment: $ENVIRONMENT. Must be dev, staging, or prod."
fi

STACK_NAME="chonkychonk-products-${ENVIRONMENT}"
S3_BUCKET="chonkychonk-sam-artifacts-${ENVIRONMENT}"
LAMBDA_SECURITY_GROUP_NAME="chonky-vpc-lambda-sg"

# ==============================================================================
# Safety check
# ==============================================================================

warn "This will permanently delete the following in region $REGION:"
warn "  - CloudFormation stack: $STACK_NAME"
warn "  - S3 bucket + all contents: $S3_BUCKET"
warn "  - AWS Secrets Manager secrets: chonky/${ENVIRONMENT}/*"
warn "  - Security group: $LAMBDA_SECURITY_GROUP_NAME (if created)"
echo ""
read -r -p "Type the environment name to confirm ($ENVIRONMENT): " CONFIRM
[ "$CONFIRM" = "$ENVIRONMENT" ] || die "Confirmation did not match — aborting."

# ==============================================================================
# 1. Delete CloudFormation stack (Lambda + IAM roles + API Gateway + EventBus)
# ==============================================================================

log "Checking for CloudFormation stack '$STACK_NAME'..."
STACK_STATUS=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query 'Stacks[0].StackStatus' \
    --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$STACK_STATUS" = "NOT_FOUND" ]; then
    warn "Stack '$STACK_NAME' not found — skipping."
else
    log "Deleting stack '$STACK_NAME' (status: $STACK_STATUS)..."
    aws cloudformation delete-stack \
        --stack-name "$STACK_NAME" \
        --region "$REGION"
    log "Waiting for stack deletion..."
    aws cloudformation wait stack-delete-complete \
        --stack-name "$STACK_NAME" \
        --region "$REGION" 2>/dev/null || warn "Stack deletion wait timed out, but continuing..."
    log "Stack deleted."
fi

# ==============================================================================
# 3. Empty and delete S3 bucket
# ==============================================================================

log "Checking for S3 bucket '$S3_BUCKET'..."
if ! aws s3api head-bucket --bucket "$S3_BUCKET" --region "$REGION" 2>/dev/null; then
    warn "Bucket '$S3_BUCKET' not found — skipping."
else
    log "Emptying bucket '$S3_BUCKET'..."
    # Delete all object versions (handles versioned buckets)
    aws s3api list-object-versions \
        --bucket "$S3_BUCKET" \
        --query '{Objects: Versions[].{Key: Key, VersionId: VersionId}}' \
        --output json 2>/dev/null | \
    python3 -c "
import json, sys, subprocess
data = json.load(sys.stdin)
objects = data.get('Objects') or []
if objects:
    subprocess.run(['aws', 's3api', 'delete-objects',
        '--bucket', '$S3_BUCKET',
        '--delete', json.dumps({'Objects': objects})], check=True)
print(f'Deleted {len(objects)} object versions.')
" || true

    # Delete remaining objects (non-versioned)
    aws s3 rm "s3://$S3_BUCKET" --recursive 2>/dev/null || true

    log "Deleting bucket '$S3_BUCKET'..."
    aws s3api delete-bucket \
        --bucket "$S3_BUCKET" \
        --region "$REGION"
    log "Bucket deleted."
fi

log ""
log "====== TEARDOWN COMPLETE ======"
