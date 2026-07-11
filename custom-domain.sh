# ==============================================================================
# API Gateway Custom Domain Setup
# ==============================================================================

DEV_EMAIL="${1}" #dev@example.com
ENVIRONMENT="${2:-dev}"
REGION="${3:-us-east-1}"
DOMAIN_NAME="api.chonkycat.ca"

log "Configuring API Gateway custom domain: $DOMAIN_NAME"

# Get API ID from CloudFormation
API_ID=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?contains(OutputKey,'Api')].Value" \
    --output text | head -n 1)

if [ -z "$API_ID" ]; then
    # fallback: derive from SAM logical API
    API_ID=$(aws apigateway get-rest-apis \
        --region "$REGION" \
        --query "items[?name=='ServerlessRestApi'].id" \
        --output text)
fi

log "API ID: $API_ID"

# Get stage name (IMPORTANT: your bug earlier)
STAGE_NAME=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Parameters[?ParameterKey=='Environment'].ParameterValue" \
    --output text)

log "Stage: $STAGE_NAME"

# Get API Gateway domain target (REGIONAL style)
API_DOMAIN_TARGET=$(aws apigateway get-domain-names \
    --region "$REGION" \
    --query "items[?domainName=='$DOMAIN_NAME'].regionalDomainName" \
    --output text)

if [ -z "$API_DOMAIN_TARGET" ] || [ "$API_DOMAIN_TARGET" = "None" ]; then
    die "Custom domain not found in API Gateway: $DOMAIN_NAME"
fi

log "API Gateway target: $API_DOMAIN_TARGET"

# Create or update base path mapping
log "Creating base path mapping..."

aws apigateway create-base-path-mapping \
    --domain-name "$DOMAIN_NAME" \
    --rest-api-id "$API_ID" \
    --stage "$STAGE_NAME" \
    --base-path "" \
    --region "$REGION" 2>/dev/null || \
aws apigateway update-base-path-mapping \
    --domain-name "$DOMAIN_NAME" \
    --base-path "" \
    --patch-operations op=replace,path=/stage,value="$STAGE_NAME" \
    --region "$REGION"

log "Custom domain mapping configured successfully"