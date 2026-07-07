#!/usr/bin/env bash
#
# Runs every events/*.json fixture through `sam local invoke` against the
# real dev DynamoDB tables (there's no local/mock DB wired up for these
# lambdas anymore since the DynamoDB migration - see shared/mock_db.py,
# which is stale leftover from the RDS days and isn't used by any lambda).
#
# Usage:
#   ./test-events.sh              # build + run every mapped event
#   ./test-events.sh --no-build   # skip `sam build` (faster re-runs)
#   ./test-events.sh orders       # only run tests whose label matches "orders"
#
set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ==============================================================================
# Config - matches deploy-products.sh's naming convention
# ==============================================================================
ENVIRONMENT="dev"
NAME_PREFIX="chonky"
REGION="$(aws configure get region || true)"
REGION="${REGION:-us-east-1}"

USERS_TABLE_NAME="${NAME_PREFIX}-users-${ENVIRONMENT}"
PRODUCTS_TABLE_NAME="${NAME_PREFIX}-products-${ENVIRONMENT}"
ORDERS_TABLE_NAME="${NAME_PREFIX}-orders-${ENVIRONMENT}"
PAYMENTS_TABLE_NAME="${NAME_PREFIX}-payments-${ENVIRONMENT}"
PROMOTIONS_TABLE_NAME="${NAME_PREFIX}-promotions-${ENVIRONMENT}"
EVENT_BUS_NAME="chonkychonk-bus"

# Fixed IDs matching the hardcoded values baked into events/*.json, so the
# fixtures below line up with what the event files already expect.
TEST_USER_ID="00000000-0000-0000-0000-000000000000"
CREATE_TEST_USER_ID="test-user-$(date +%s)"  # unique user for orders-create test
TEST_PRODUCT_IDS=("00000000-0000-0000-0000-000000000001" "00000000-0000-0000-0000-000000000002")
TEST_ORDER_ID="00000000-0000-0000-0000-000000000000"
CREATED_ORDER_IDS=()  # order ids created live by the orders-create test, cleaned up too

SKIP_BUILD=false
FILTER=""
for arg in "$@"; do
  case "$arg" in
    --no-build) SKIP_BUILD=true ;;
    --*) die "Unknown flag: $arg" ;;
    *) FILTER="$arg" ;;
  esac
done

command -v sam >/dev/null || die "sam CLI not found on PATH"
command -v jq  >/dev/null || die "jq not found on PATH"

log "Using region: $REGION"

for TABLE in "$USERS_TABLE_NAME" "$PRODUCTS_TABLE_NAME" "$ORDERS_TABLE_NAME" \
             "$PAYMENTS_TABLE_NAME" "$PROMOTIONS_TABLE_NAME"; do
  if ! aws dynamodb describe-table --table-name "$TABLE" --region "$REGION" >/dev/null 2>&1; then
    die "DynamoDB table '$TABLE' not found in $REGION. Are your AWS credentials pointed at the right account?"
  fi
done

if [ "$SKIP_BUILD" = false ]; then
  log "Running sam build..."
  sam build >/tmp/sam-build.log 2>&1 || { cat /tmp/sam-build.log; die "sam build failed"; }
fi

# ==============================================================================
# Seed data — the dev DynamoDB tables start out empty, so orders/get/update/
# delete/payments tests need a user, some products, and a fixed-id order to
# exist first. Torn down at the end regardless of pass/fail (see trap below).
# ==============================================================================
seed_data() {
  log "Seeding test fixtures into DynamoDB..."

  aws dynamodb put-item --region "$REGION" --table-name "$USERS_TABLE_NAME" --item '{
    "user_id": {"S": "'"$TEST_USER_ID"'"},
    "email": {"S": "test-events@example.com"},
    "name": {"S": "Test Events User"}
  }' || die "Failed to seed test user"

  aws dynamodb put-item --region "$REGION" --table-name "$USERS_TABLE_NAME" --item '{
    "user_id": {"S": "'"$CREATE_TEST_USER_ID"'"},
    "email": {"S": "test-create-'"$CREATE_TEST_USER_ID"'@example.com"},
    "name": {"S": "Test Create User"}
  }' || die "Failed to seed create test user"

  for PID in "${TEST_PRODUCT_IDS[@]}"; do
    aws dynamodb put-item --region "$REGION" --table-name "$PRODUCTS_TABLE_NAME" --item '{
      "product_id": {"S": "'"$PID"'"},
      "name": {"S": "Test Product '"$PID"'"},
      "price": {"N": "19.99"},
      "qty": {"N": "100"},
      "active": {"BOOL": true}
    }' || die "Failed to seed test product $PID"
  done

  NOW_ISO="$(date -u +%Y-%m-%dT%H:%M:%S.000Z)"
  aws dynamodb put-item --region "$REGION" --table-name "$ORDERS_TABLE_NAME" --item '{
    "order_id": {"S": "'"$TEST_ORDER_ID"'"},
    "sk": {"S": "ORDER"},
    "user_id": {"S": "'"$TEST_USER_ID"'"},
    "status": {"S": "pending"},
    "subtotal": {"S": "19.99"},
    "tax_amount": {"S": "2.60"},
    "shipping_amount": {"S": "10.00"},
    "total_amount": {"S": "32.59"},
    "customer_notes": {"S": "seeded by test-events.sh"},
    "shipping_name": {"S": "Test Events User"},
    "shipping_address1": {"S": "123 Test St"},
    "shipping_city": {"S": "Toronto"},
    "shipping_province": {"S": "ON"},
    "shipping_postal_code": {"S": "M5V 3A8"},
    "shipping_country": {"S": "Canada"},
    "created_at": {"S": "'"$NOW_ISO"'"},
    "updated_at": {"S": "'"$NOW_ISO"'"}
  }' || die "Failed to seed test order"

  aws dynamodb put-item --region "$REGION" --table-name "$ORDERS_TABLE_NAME" --item '{
    "order_id": {"S": "'"$TEST_ORDER_ID"'"},
    "sk": {"S": "ITEM#0000"},
    "product_id": {"S": "1"},
    "quantity": {"N": "1"},
    "unit_price": {"S": "19.99"},
    "line_total": {"S": "19.99"},
    "name_snapshot": {"S": "Test Product 1"}
  }' || die "Failed to seed test order item"
}

cleanup_seed_data() {
  log "Cleaning up seeded test fixtures..."

  aws dynamodb delete-item --region "$REGION" --table-name "$ORDERS_TABLE_NAME" \
    --key '{"order_id": {"S": "'"$TEST_ORDER_ID"'"}, "sk": {"S": "ORDER"}}' >/dev/null 2>&1
  aws dynamodb delete-item --region "$REGION" --table-name "$ORDERS_TABLE_NAME" \
    --key '{"order_id": {"S": "'"$TEST_ORDER_ID"'"}, "sk": {"S": "ITEM#0000"}}' >/dev/null 2>&1

  for ORDER_ID in "${CREATED_ORDER_IDS[@]}"; do
    for SK in ORDER ITEM#0000 ITEM#0001 ITEM#0002; do
      aws dynamodb delete-item --region "$REGION" --table-name "$ORDERS_TABLE_NAME" \
        --key '{"order_id": {"S": "'"$ORDER_ID"'"}, "sk": {"S": "'"$SK"'"}}' >/dev/null 2>&1
    done
  done

  for PID in "${TEST_PRODUCT_IDS[@]}"; do
    aws dynamodb delete-item --region "$REGION" --table-name "$PRODUCTS_TABLE_NAME" \
      --key '{"product_id": {"S": "'"$PID"'"}}' >/dev/null 2>&1
  done

  aws dynamodb delete-item --region "$REGION" --table-name "$USERS_TABLE_NAME" \
    --key '{"user_id": {"S": "'"$TEST_USER_ID"'"}}' >/dev/null 2>&1

  aws dynamodb delete-item --region "$REGION" --table-name "$USERS_TABLE_NAME" \
    --key '{"user_id": {"S": "'"$CREATE_TEST_USER_ID"'"}}' >/dev/null 2>&1
}

seed_data

PARAM_OVERRIDES="UsersTableName=${USERS_TABLE_NAME} ProductsTableName=${PRODUCTS_TABLE_NAME} OrdersTableName=${ORDERS_TABLE_NAME} PaymentsTableName=${PAYMENTS_TABLE_NAME} PromotionsTableName=${PROMOTIONS_TABLE_NAME} EventBusName=${EVENT_BUS_NAME}"

# StripeIntentFunction is invoked cross-network (boto3 lambda.invoke) by
# PaymentsApiFunction rather than in-process, so !GetAtt StripeIntentFunction.Arn
# (a fake local ARN under `sam local invoke`) has to be swapped for the real
# deployed function's ARN via --env-vars, or the invoke call 404s.
STRIPE_INTENT_ARN="$(aws lambda get-function \
  --function-name "chonkychonk-stripe-intent-${ENVIRONMENT}" \
  --region "$REGION" --query 'Configuration.FunctionArn' --output text 2>/dev/null || true)"

ENV_VARS_FILE="$(mktemp)"
TEMP_EVENT_FILE="$(mktemp)"
trap 'rm -f "$ENV_VARS_FILE" "$TEMP_EVENT_FILE"; cleanup_seed_data' EXIT

# Generate temporary orders-create event with dynamic test user ID
# Create a template first, then substitute the user ID
cat > "$TEMP_EVENT_FILE" <<'TEMP_EOF'
{
  "httpMethod": "POST",
  "path": "/orders",
  "headers": {
    "X-User-Id": "TEST_USER_ID_PLACEHOLDER",
    "Content-Type": "application/json"
  },
  "body": "{\"user_id\": \"TEST_USER_ID_PLACEHOLDER\", \"customer_email\": \"test@example.com\", \"items\": [{\"product_id\": \"5cefc246-9c78-5c86-a44a-d117640b079f\", \"quantity\": 2}, {\"product_id\": \"aa999047-ccb4-5fba-b9b8-741127a820b4\", \"quantity\": 1}], \"shipping\": {\"name\": \"John Doe\", \"address1\": \"123 Main St\", \"address2\": \"Apt 4B\", \"city\": \"Toronto\", \"province\": \"ON\", \"postal_code\": \"M5V 3A8\", \"country\": \"Canada\"}, \"customer_notes\": \"Please leave at door\"}"
}
TEMP_EOF
sed -i "s|TEST_USER_ID_PLACEHOLDER|$CREATE_TEST_USER_ID|g" "$TEMP_EVENT_FILE"

# NOTE: PaymentsApiFunction reads USERS_TABLE_NAME (lambdas/payments_api/db.py)
# but template.yaml doesn't set that env var or grant read access to the users
# table for this function - that's a bug in the deployed stack, not just a
# local-testing gap. Overriding it here so the test can still run locally.
cat > "$ENV_VARS_FILE" <<EOF
{
  "PaymentsApiFunction": {
    "ORDERS_TABLE_NAME": "${ORDERS_TABLE_NAME}",
    "PAYMENTS_TABLE_NAME": "${PAYMENTS_TABLE_NAME}",
    "USERS_TABLE_NAME": "${USERS_TABLE_NAME}",
    "EVENT_BUS_NAME": "${EVENT_BUS_NAME}",
    "STRIPE_INTENT_FUNCTION_ARN": "${STRIPE_INTENT_ARN}"
  }
}
EOF

# ==============================================================================
# event file | function logical id | label
# ==============================================================================
TESTS=(
  "events/orders-create.json|OrdersFunction|POST /orders (create)"
  "events/orders-get.json|OrdersFunction|GET /orders/{id}"
  "events/orders-update.json|OrdersFunction|PUT /orders/{id}"
  "events/orders-delete.json|OrdersFunction|DELETE /orders/{id}"
  "events/payments-charge.json|PaymentsApiFunction|POST /payments (charge)"
  "events/payments-api.json|PaymentsApiFunction|POST /payments (legacy fixture)"
  "events/stripe-webhook.json|StripeWebhookFunction|POST /webhook"
)

LOG_DIR="$(mktemp -d)"
PASS=0
FAIL=0
SKIP=0

for entry in "${TESTS[@]}"; do
  IFS='|' read -r EVENT_FILE FUNCTION LABEL <<< "$entry"

  if [ -n "$FILTER" ] && [[ "$EVENT_FILE $LABEL $FUNCTION" != *"$FILTER"* ]]; then
    continue
  fi

  # Use temporary generated event for orders-create test
  ACTUAL_EVENT_FILE="$EVENT_FILE"
  if [ "$EVENT_FILE" = "events/orders-create.json" ]; then
    ACTUAL_EVENT_FILE="$TEMP_EVENT_FILE"
  fi

  if [ ! -f "$ACTUAL_EVENT_FILE" ]; then
    warn "Skipping $LABEL — $ACTUAL_EVENT_FILE not found"
    SKIP=$((SKIP + 1))
    continue
  fi

  if [ "$FUNCTION" = "PaymentsApiFunction" ] && [ -z "$STRIPE_INTENT_ARN" ]; then
    warn "Skipping $LABEL — couldn't resolve chonkychonk-stripe-intent-${ENVIRONMENT} ARN"
    SKIP=$((SKIP + 1))
    continue
  fi

  echo ""
  echo "=== $LABEL  ($EVENT_FILE -> $FUNCTION) ==="

  LOG_FILE="$LOG_DIR/$(basename "$EVENT_FILE").log"
  OUTPUT="$(sam local invoke "$FUNCTION" \
    -e "$ACTUAL_EVENT_FILE" \
    --region "$REGION" \
    --parameter-overrides "$PARAM_OVERRIDES" \
    --env-vars "$ENV_VARS_FILE" 2>"$LOG_FILE")"
  EXIT_CODE=$?

  RESPONSE_LINE="$(echo "$OUTPUT" | tail -n 1)"

  if [ $EXIT_CODE -ne 0 ]; then
    echo -e "${RED}✗ FAIL${NC} — sam local invoke exited $EXIT_CODE"
    tail -n 15 "$LOG_FILE"
    FAIL=$((FAIL + 1))
    continue
  fi

  if ! echo "$RESPONSE_LINE" | jq -e . >/dev/null 2>&1; then
    echo -e "${RED}✗ FAIL${NC} — response wasn't valid JSON:"
    echo "$RESPONSE_LINE"
    FAIL=$((FAIL + 1))
    continue
  fi

  if echo "$RESPONSE_LINE" | jq -e '.errorMessage' >/dev/null 2>&1; then
    echo -e "${RED}✗ FAIL${NC} — unhandled exception in Lambda:"
    echo "$RESPONSE_LINE" | jq .
    FAIL=$((FAIL + 1))
    continue
  fi

  STATUS_CODE="$(echo "$RESPONSE_LINE" | jq -r '.statusCode // "n/a"')"
  BODY="$(echo "$RESPONSE_LINE" | jq -r '.body // empty')"
  echo -e "${GREEN}✓ RAN${NC} — statusCode=$STATUS_CODE"
  if [ -n "$BODY" ]; then
    echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
  fi

  if [ "$EVENT_FILE" = "events/orders-create.json" ] && [ -n "$BODY" ]; then
    NEW_ORDER_ID="$(echo "$BODY" | jq -r '.order.order_id // empty' 2>/dev/null)"
    [ -n "$NEW_ORDER_ID" ] && CREATED_ORDER_IDS+=("$NEW_ORDER_ID")
  fi

  PASS=$((PASS + 1))
done

echo ""
echo "================================"
echo "Ran: $((PASS + FAIL))   Passed: $PASS   Failed: $FAIL   Skipped: $SKIP"
echo "Full logs: $LOG_DIR"
echo "================================"

[ "$FAIL" -eq 0 ]
