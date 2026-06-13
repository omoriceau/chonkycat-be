#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Deploy script for products lambda only
# ==============================================================================

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

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

log "Deploying products lambda to $ENVIRONMENT in $REGION"

# Get RDS endpoint
log "Fetching RDS endpoint..."
DB_HOST=$(aws rds describe-db-instances \
    --db-instance-identifier chonky-instance \
    --region "$REGION" \
    --query 'DBInstances[0].Endpoint.Address' \
    --output text)

if [ -z "$DB_HOST" ] || [ "$DB_HOST" = "None" ]; then
    die "Could not find RDS instance 'chonky-instance' in $REGION"
fi

log "RDS endpoint: $DB_HOST"

# Database credentials (from aws-setup.sh)
DB_USER="chonky_admin"
DB_PASSWORD="Ch0nky_Secure_P4ss_2026!"
DB_NAME="chonky"
DB_PORT="5432"

# VPC configuration — required for Lambda to access RDS
log "Fetching VPC configuration..."
VPC_ID=$(aws ec2 describe-vpcs \
    --filters "Name=tag:Name,Values=chonky-vpc" \
    --region "$REGION" \
    --query 'Vpcs[0].VpcId' \
    --output text)

if [ -z "$VPC_ID" ] || [ "$VPC_ID" = "None" ]; then
    die "Could not find VPC with tag Name=chonky-vpc in $REGION"
fi

SUBNET_IDS=$(aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:Name,Values=chonky-subnet-a,chonky-subnet-b" \
    --region "$REGION" \
    --query 'Subnets[*].SubnetId' \
    --output text | tr '\t' ',')

if [ -z "$SUBNET_IDS" ]; then
    die "Could not find private subnets in VPC $VPC_ID"
fi

SECURITY_GROUP_ID=$(aws ec2 describe-security-groups \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:Name,Values=chonky-lambda-sg" \
    --region "$REGION" \
    --query 'SecurityGroups[0].GroupId' \
    --output text)

if [ -z "$SECURITY_GROUP_ID" ] || [ "$SECURITY_GROUP_ID" = "None" ]; then
    log "Security group chonky-lambda-sg not found, creating it..."
    SECURITY_GROUP_ID=$(aws ec2 create-security-group \
        --group-name chonky-lambda-sg \
        --description "Security group for ChonkyChonk Lambda functions" \
        --vpc-id "$VPC_ID" \
        --region "$REGION" \
        --query 'GroupId' \
        --output text)
    
    log "Created security group: $SECURITY_GROUP_ID"
    
    # Tag it
    aws ec2 create-tags \
        --resources "$SECURITY_GROUP_ID" \
        --tags "Key=Name,Value=chonky-lambda-sg" \
        --region "$REGION"
    
    # Get the RDS security group ID
    DB_SECURITY_GROUP_ID=$(aws ec2 describe-security-groups \
        --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=chonky-db-sg" \
        --region "$REGION" \
        --query 'SecurityGroups[0].GroupId' \
        --output text)
    
    if [ -z "$DB_SECURITY_GROUP_ID" ] || [ "$DB_SECURITY_GROUP_ID" = "None" ]; then
        die "Could not find RDS security group chonky-db-sg"
    fi
    
    # Allow egress from Lambda to RDS on port 5432
    log "Configuring security group rules..."
    aws ec2 authorize-security-group-egress \
        --group-id "$SECURITY_GROUP_ID" \
        --protocol tcp \
        --port 5432 \
        --source-group "$DB_SECURITY_GROUP_ID" \
        --region "$REGION" 2>/dev/null || warn "Egress rule may already exist"
    
    # Allow RDS to accept from Lambda
    aws ec2 authorize-security-group-ingress \
        --group-id "$DB_SECURITY_GROUP_ID" \
        --protocol tcp \
        --port 5432 \
        --source-group "$SECURITY_GROUP_ID" \
        --region "$REGION" 2>/dev/null || warn "Ingress rule may already exist"
fi

log "VPC configuration:"
log "  VPC ID: $VPC_ID"
log "  Subnets: $SUBNET_IDS"
log "  Security Group: $SECURITY_GROUP_ID"

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

# ==============================================================================
# Build
# ==============================================================================

log "Building lambda package..."
# sam build -t template.products.yaml
sam build

# ==============================================================================
# Deploy
# ==============================================================================

STACK_NAME="chonkychonk-products-${ENVIRONMENT}"

log "Deploying stack: $STACK_NAME"

sam deploy \
    --template-file .aws-sam/build/template.yaml \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --s3-bucket "$S3_BUCKET" \
    --capabilities CAPABILITY_IAM \
    --no-confirm-changeset \
    --parameter-overrides \
        Environment="$ENVIRONMENT" \
        DBHost="$DB_HOST" \
        DBPort="$DB_PORT" \
        DBUser="$DB_USER" \
        DBPassword="$DB_PASSWORD" \
        DBName="$DB_NAME" \
        VpcId="$VPC_ID" \
        SubnetIds="$SUBNET_IDS" \
        SecurityGroupId="$SECURITY_GROUP_ID"

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
