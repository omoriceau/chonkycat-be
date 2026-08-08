#!/usr/bin/env bash
#
# Runs every events/*.json fixture through `sam local invoke` against the
# real dev DynamoDB tables (there's no local/mock DB wired up for these
# lambdas anymore since the DynamoDB migration).
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

# Product-scenario test IDs. ProductsFunction is read-only (GET /products,
# GET /products/{productid}) - there's no create/update/delete API for
# products yet - so "add" / "soft delete" / "edit price" are simulated by
# seeding (and, for edit-price, later mutating) rows directly in DynamoDB,
# then asserting the read paths reflect the expected state.
PRODUCT_ADDED_ID="00000000-0000-0000-0000-000000000010"
PRODUCT_SOFT_DELETED_ID="00000000-0000-0000-0000-000000000011"
PRODUCT_LOW_STOCK_ID="00000000-0000-0000-0000-000000000012"
PRODUCT_NO_STOCK_ID="00000000-0000-0000-0000-000000000013"
PRODUCT_EDIT_PRICE_ID="00000000-0000-0000-0000-000000000014"
PRODUCT_SCENARIO_IDS=("$PRODUCT_ADDED_ID" "$PRODUCT_SOFT_DELETED_ID" "$PRODUCT_LOW_STOCK_ID" "$PRODUCT_NO_STOCK_ID" "$PRODUCT_EDIT_PRICE_ID")

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

  aws dynamodb put-item --region "$REGION" --table-name "$PRODUCTS_TABLE_NAME" --item '{
    "product_id": {"S": "'"$PRODUCT_ADDED_ID"'"},
    "sku": {"S": "TEST-ADD-1"},
    "name": {"S": "Freshly Added Product"},
    "category": {"S": "test-category"},
    "price": {"N": "24.99"},
    "qty": {"N": "50"},
    "low_stock_threshold": {"N": "10"},
    "active": {"BOOL": true}
  }' || die "Failed to seed products-add fixture"

  aws dynamodb put-item --region "$REGION" --table-name "$PRODUCTS_TABLE_NAME" --item '{
    "product_id": {"S": "'"$PRODUCT_SOFT_DELETED_ID"'"},
    "sku": {"S": "TEST-DEL-1"},
    "name": {"S": "Soft Deleted Product"},
    "category": {"S": "test-category"},
    "price": {"N": "15.00"},
    "qty": {"N": "20"},
    "low_stock_threshold": {"N": "5"},
    "active": {"BOOL": false}
  }' || die "Failed to seed products-soft-delete fixture"

  aws dynamodb put-item --region "$REGION" --table-name "$PRODUCTS_TABLE_NAME" --item '{
    "product_id": {"S": "'"$PRODUCT_LOW_STOCK_ID"'"},
    "sku": {"S": "TEST-LOW-1"},
    "name": {"S": "Low Stock Product"},
    "category": {"S": "test-category"},
    "price": {"N": "8.50"},
    "qty": {"N": "3"},
    "low_stock_threshold": {"N": "5"},
    "reorder_flag": {"S": "true"},
    "active": {"BOOL": true}
  }' || die "Failed to seed products-low-stock fixture"

  aws dynamodb put-item --region "$REGION" --table-name "$PRODUCTS_TABLE_NAME" --item '{
    "product_id": {"S": "'"$PRODUCT_NO_STOCK_ID"'"},
    "sku": {"S": "TEST-OOS-1"},
    "name": {"S": "Out Of Stock Product"},
    "category": {"S": "test-category"},
    "price": {"N": "12.00"},
    "qty": {"N": "0"},
    "low_stock_threshold": {"N": "5"},
    "reorder_flag": {"S": "true"},
    "active": {"BOOL": true}
  }' || die "Failed to seed products-no-stock fixture"

  aws dynamodb put-item --region "$REGION" --table-name "$PRODUCTS_TABLE_NAME" --item '{
    "product_id": {"S": "'"$PRODUCT_EDIT_PRICE_ID"'"},
    "sku": {"S": "TEST-EDIT-1"},
    "name": {"S": "Edit Price Product"},
    "category": {"S": "test-category"},
    "price": {"N": "9.99"},
    "qty": {"N": "40"},
    "low_stock_threshold": {"N": "5"},
    "active": {"BOOL": true}
  }' || die "Failed to seed products-edit-price fixture"

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

  for PID in "${PRODUCT_SCENARIO_IDS[@]}"; do
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

# ==============================================================================
# Product scenario tests — ProductsFunction only exposes GET /products and
# GET /products/{productid} (see template.yaml). There's no create/update/
# delete API for products, so "add" / "soft delete" / "edit price" are
# simulated by seeding (and, for edit-price, later mutating) rows directly
# in DynamoDB in seed_data() above, and these tests assert the read paths
# reflect the expected state — unlike the generic loop above, which only
# checks that the Lambda ran without throwing.
# ==============================================================================

invoke_products() {
  # $1 = event file. Sets RESP_STATUS / RESP_BODY on success.
  local EVENT_FILE="$1"
  local LOG_FILE="$LOG_DIR/$(basename "$EVENT_FILE").log"
  local OUTPUT EXIT_CODE RESPONSE_LINE
  OUTPUT="$(sam local invoke "ProductsFunction" \
    -e "$EVENT_FILE" \
    --region "$REGION" \
    --parameter-overrides "$PARAM_OVERRIDES" \
    --env-vars "$ENV_VARS_FILE" 2>"$LOG_FILE")"
  EXIT_CODE=$?
  RESPONSE_LINE="$(echo "$OUTPUT" | tail -n 1)"

  if [ $EXIT_CODE -ne 0 ]; then
    echo -e "${RED}  ✗${NC} sam local invoke exited $EXIT_CODE"
    tail -n 15 "$LOG_FILE"
    return 1
  fi
  if ! echo "$RESPONSE_LINE" | jq -e . >/dev/null 2>&1; then
    echo -e "${RED}  ✗${NC} response wasn't valid JSON: $RESPONSE_LINE"
    return 1
  fi
  if echo "$RESPONSE_LINE" | jq -e '.errorMessage' >/dev/null 2>&1; then
    echo -e "${RED}  ✗${NC} unhandled exception in Lambda:"
    echo "$RESPONSE_LINE" | jq .
    return 1
  fi

  RESP_STATUS="$(echo "$RESPONSE_LINE" | jq -r '.statusCode // "n/a"')"
  RESP_BODY="$(echo "$RESPONSE_LINE" | jq -r '.body // empty')"
  return 0
}

check_status() {
  local desc="$1" expected="$2"
  if [ "$RESP_STATUS" = "$expected" ]; then
    echo -e "${GREEN}  ✓${NC} $desc"
    return 0
  fi
  echo -e "${RED}  ✗${NC} $desc (expected statusCode $expected, got $RESP_STATUS)"
  return 1
}

check_body() {
  local desc="$1" filter="$2"
  if echo "$RESP_BODY" | jq -e "$filter" >/dev/null 2>&1; then
    echo -e "${GREEN}  ✓${NC} $desc"
    return 0
  fi
  echo -e "${RED}  ✗${NC} $desc"
  echo "$RESP_BODY" | jq . 2>/dev/null || echo "$RESP_BODY"
  return 1
}

test_products_add() {
  invoke_products "events/products-get-added.json" || return 1
  local rc=0
  check_status "GET /products/{id} returns 200"           200 || rc=1
  check_body   "id matches the seeded fixture"            ".data.id == \"$PRODUCT_ADDED_ID\"" || rc=1
  check_body   "name matches the seeded fixture"          '.data.name == "Freshly Added Product"' || rc=1
  check_body   "price matches the seeded fixture"         '.data.price == 24.99' || rc=1
  check_body   "product is active"                        '.data.active == true' || rc=1

  invoke_products "events/products-list.json" || return 1
  check_body "newly added product appears in the default catalog listing" \
    "[.data[].id] | index(\"$PRODUCT_ADDED_ID\") != null" || rc=1

  return $rc
}

test_products_soft_delete() {
  invoke_products "events/products-list.json" || return 1
  local rc=0
  check_body "soft-deleted product is hidden from the default (active-only) listing" \
    "([.data[].id] | index(\"$PRODUCT_SOFT_DELETED_ID\")) == null" || rc=1

  invoke_products "events/products-list-show-all.json" || return 1
  check_body "soft-deleted product reappears when show_all=true" \
    "[.data[].id] | index(\"$PRODUCT_SOFT_DELETED_ID\") != null" || rc=1

  invoke_products "events/products-get-soft-deleted.json" || return 1
  check_status "GET /products/{id} still returns 200 for a soft-deleted product" 200 || rc=1
  check_body   "direct lookup reports active: false"                             '.data.active == false' || rc=1

  return $rc
}

test_products_low_stock() {
  invoke_products "events/products-get-low-stock.json" || return 1
  local rc=0
  check_body "current_stock reflects the seeded low quantity" '.data.current_stock == 3' || rc=1
  check_body "is_low_stock is true when qty <= threshold"     '.data.is_low_stock == true' || rc=1

  invoke_products "events/products-list-low-stock.json" || return 1
  check_body "low-stock product appears under ?low_stock=true" \
    "[.data[].id] | index(\"$PRODUCT_LOW_STOCK_ID\") != null" || rc=1

  return $rc
}

test_products_no_stock() {
  invoke_products "events/products-get-no-stock.json" || return 1
  local rc=0
  check_body "current_stock is 0"             '.data.current_stock == 0' || rc=1
  check_body "zero stock counts as low stock" '.data.is_low_stock == true' || rc=1

  invoke_products "events/products-list-low-stock.json" || return 1
  check_body "out-of-stock product appears under ?low_stock=true" \
    "[.data[].id] | index(\"$PRODUCT_NO_STOCK_ID\") != null" || rc=1

  invoke_products "events/products-list.json" || return 1
  check_body "out-of-stock product still shows in the default catalog listing (no auto-hide on qty=0)" \
    "[.data[].id] | index(\"$PRODUCT_NO_STOCK_ID\") != null" || rc=1

  return $rc
}

test_products_edit_price() {
  invoke_products "events/products-get-edit-price.json" || return 1
  local rc=0
  check_body "price before edit matches the seeded value" '.data.price == 9.99' || rc=1

  if ! aws dynamodb update-item --region "$REGION" --table-name "$PRODUCTS_TABLE_NAME" \
    --key '{"product_id": {"S": "'"$PRODUCT_EDIT_PRICE_ID"'"}}' \
    --update-expression 'SET price = :p' \
    --expression-attribute-values '{":p": {"N": "14.99"}}' >/dev/null; then
    echo -e "${RED}  ✗${NC} failed to apply price edit via update-item"
    return 1
  fi

  invoke_products "events/products-get-edit-price.json" || return 1
  check_body "price after edit reflects the new value" '.data.price == 14.99' || rc=1

  return $rc
}

run_product_test() {
  local LABEL="$1" TEST_FN="$2"
  if [ -n "$FILTER" ] && [[ "$LABEL" != *"$FILTER"* ]]; then
    SKIP=$((SKIP + 1))
    return
  fi

  echo ""
  echo "=== $LABEL ==="
  if "$TEST_FN"; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASS=$((PASS + 1))
  else
    echo -e "${RED}✗ FAIL${NC}"
    FAIL=$((FAIL + 1))
  fi
}

run_product_test "products-add"         test_products_add
run_product_test "products-soft-delete" test_products_soft_delete
run_product_test "products-low-stock"   test_products_low_stock
run_product_test "products-no-stock"    test_products_no_stock
run_product_test "products-edit-price"  test_products_edit_price

echo ""
echo "================================"
echo "Ran: $((PASS + FAIL))   Passed: $PASS   Failed: $FAIL   Skipped: $SKIP"
echo "Full logs: $LOG_DIR"
echo "================================"

[ "$FAIL" -eq 0 ]
