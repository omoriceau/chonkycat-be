#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Minimal, non-destructive deploy for CI: build + deploy the SAM stack, and
# nothing else.
#
# samconfig.toml's [<environment>.deploy.parameters] section is the single
# source of truth for stack_name / region / s3_bucket / parameter_overrides
# — this script reads it via `sam deploy --config-env`, it does not rebuild
# its own copy of that list. Keep it in sync there, not here.
#
# This is deliberately a subset of deploy-products.sh. That script also
# provisions SES domains, the S3 artifacts bucket, the EventBridge bus, an
# API Gateway custom domain mapping, and an imperative (non-CloudFormation)
# EventBridge rule — none of which belong in an unattended CI run on every
# push. It also auto-deletes the CloudFormation stack if it's stuck in
# UPDATE_ROLLBACK_FAILED, which is not something that should ever happen
# without a human deciding to do it.
#
# This script instead assumes the target environment's infrastructure
# (DynamoDB tables, S3 bucket, EventBridge bus, SES domain, custom domain)
# already exists — run deploy-products.sh once, manually, to provision a
# new environment — and just fails with a clear message if a prerequisite
# is missing or still has a TODO-* placeholder, rather than silently
# creating or deleting anything.
#
# Usage: $0 [--environment dev|staging|prod] [--region REGION]
# --region overrides samconfig.toml's region for this run only.
# ==============================================================================

log()  { echo -e "[INFO]  $*"; }
warn() { echo -e "[WARN]  $*"; }
die()  { echo -e "[ERROR] $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/samconfig.toml"

ENVIRONMENT="dev"
REGION_ARG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --environment=*|--env=*) ENVIRONMENT="${1#*=}"; shift ;;
    --environment|--env)     [[ $# -ge 2 ]] || die "$1 requires a value."; ENVIRONMENT="$2"; shift 2 ;;
    --region=*)               REGION_ARG="${1#*=}"; shift ;;
    --region)                 [[ $# -ge 2 ]] || die "$1 requires a value."; REGION_ARG="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--environment dev|staging|prod] [--region REGION]"
      exit 0
      ;;
    *) die "Unknown argument: '$1'." ;;
  esac
done

case "$ENVIRONMENT" in
  dev|staging|prod) ;;
  *) die "Invalid environment: '$ENVIRONMENT'. Must be dev, staging, or prod." ;;
esac

[[ -f "$CONFIG_FILE" ]] || die "samconfig.toml not found at $CONFIG_FILE."

# ==============================================================================
# Pull this environment's config out of samconfig.toml — stack_name, region,
# s3_bucket, and the DynamoDB table names out of parameter_overrides — so
# the pre-flight checks below know what to look for. sam deploy itself reads
# the full parameter_overrides list straight from the file later; this is
# just the subset this script needs for its own verification.
# ==============================================================================
CONFIG_JSON=$(python3 - "$CONFIG_FILE" "$ENVIRONMENT" <<'PYEOF'
import sys, json, tomllib

config_file, environment = sys.argv[1], sys.argv[2]
with open(config_file, "rb") as f:
    cfg = tomllib.load(f)

section = cfg.get(environment, {}).get("deploy", {}).get("parameters")
if section is None:
    print(json.dumps({"error": f"No [{environment}.deploy.parameters] section in samconfig.toml."}))
    sys.exit(0)

overrides = dict(p.split("=", 1) for p in section.get("parameter_overrides", []))
todo = sorted(k for k, v in overrides.items() if "TODO-" in v)

print(json.dumps({
    "stack_name": section.get("stack_name", ""),
    "region": section.get("region", ""),
    "s3_bucket": section.get("s3_bucket", ""),
    "event_bus_name": overrides.get("EventBusName", "chonkychonk-bus"),
    "tables": {
        k: overrides.get(k, "")
        for k in ("UsersTableName", "ProductsTableName", "OrdersTableName",
                   "PaymentsTableName", "PromotionsTableName")
    },
    "todo_placeholders": todo,
}))
PYEOF
)

CONFIG_ERROR=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('error',''))" "$CONFIG_JSON")
[[ -z "$CONFIG_ERROR" ]] || die "$CONFIG_ERROR Add one (see the [dev...] section for the shape) before deploying $ENVIRONMENT."

TODO_PLACEHOLDERS=$(python3 -c "import json,sys; print(' '.join(json.loads(sys.argv[1])['todo_placeholders']))" "$CONFIG_JSON")
if [[ -n "$TODO_PLACEHOLDERS" ]]; then
  die "samconfig.toml's [$ENVIRONMENT.deploy.parameters] still has TODO-* placeholder values for: $TODO_PLACEHOLDERS. Fill in the real values before deploying $ENVIRONMENT."
fi

STACK_NAME=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['stack_name'])" "$CONFIG_JSON")
CONFIG_REGION=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['region'])" "$CONFIG_JSON")
S3_BUCKET=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['s3_bucket'])" "$CONFIG_JSON")
EVENT_BUS_NAME=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['event_bus_name'])" "$CONFIG_JSON")

[[ -n "$STACK_NAME" ]] || die "samconfig.toml's [$ENVIRONMENT.deploy.parameters] is missing stack_name."
[[ -n "$S3_BUCKET" ]]  || die "samconfig.toml's [$ENVIRONMENT.deploy.parameters] is missing s3_bucket."

REGION="${REGION_ARG:-$CONFIG_REGION}"
if [[ -z "$REGION" ]]; then
  REGION="$(aws configure get region || true)"
  if [[ -z "$REGION" ]]; then
    REGION="us-east-1"
    warn "No region in samconfig.toml, no --region passed, and no AWS CLI default region configured — falling back to us-east-1."
  fi
fi

log "Deploying $STACK_NAME ($ENVIRONMENT) to $REGION"

# ==============================================================================
# Pre-flight: verify the infra this deploy depends on already exists.
# Nothing below this point creates anything — it only checks and dies with
# a clear message if a prerequisite is missing. Run deploy-products.sh
# manually to provision a new environment first.
# ==============================================================================

log "Verifying DynamoDB tables exist in $REGION..."
for KEY in UsersTableName ProductsTableName OrdersTableName PaymentsTableName PromotionsTableName; do
  TABLE=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['tables'][sys.argv[2]])" "$CONFIG_JSON" "$KEY")
  [[ -n "$TABLE" ]] || die "samconfig.toml's [$ENVIRONMENT.deploy.parameters] is missing $KEY in parameter_overrides."
  aws dynamodb describe-table --table-name "$TABLE" --region "$REGION" >/dev/null 2>&1 \
      || die "DynamoDB table '$TABLE' not found in $REGION. Provision $ENVIRONMENT's infra first (see deploy-products.sh / CICD.md)."
  log "  found: $TABLE"
done

log "Verifying S3 artifacts bucket exists: $S3_BUCKET"
aws s3api head-bucket --bucket "$S3_BUCKET" --region "$REGION" 2>/dev/null \
    || die "S3 bucket '$S3_BUCKET' not found. Provision $ENVIRONMENT's infra first (see deploy-products.sh / CICD.md)."

log "Verifying EventBridge event bus exists: $EVENT_BUS_NAME"
aws events describe-event-bus --name "$EVENT_BUS_NAME" --region "$REGION" >/dev/null 2>&1 \
    || die "EventBridge bus '$EVENT_BUS_NAME' not found. Provision $ENVIRONMENT's infra first (see deploy-products.sh / CICD.md)."

# ==============================================================================
# Build + deploy — parameters come from samconfig.toml via --config-env,
# not from a hand-maintained list here.
# ==============================================================================

log "Building lambda package..."
sam build

log "Deploying stack: $STACK_NAME"

# No stack-status pre-check, no auto-delete/auto-remediate: if the stack is
# in a state sam deploy can't update (e.g. UPDATE_ROLLBACK_FAILED), let it
# fail with CloudFormation's own error. Deciding to delete a stack is a
# human call, not something a CI run should do unattended.
sam deploy \
    --config-env "$ENVIRONMENT" \
    --region "$REGION" \
    --no-confirm-changeset

log "Deployment complete!"
aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs' \
    --output table
