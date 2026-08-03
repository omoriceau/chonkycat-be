#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Looks up the real Cognito pool/client IDs for one environment in AWS and
# writes them into samconfig.toml, in place, without disturbing comments or
# formatting anywhere else in the file.
#
# Usage: ./update-samconfig.sh [--dry-run] [--region REGION] <environment> [KEY=VALUE ...]
#
# For <environment>, looks up (by name, in Cognito):
#   chonky-admins-<environment>     -> CognitoUserPoolId
#   chonky-customers-<environment>  -> CustomerCognitoUserPoolId
#   (that pool's app client)        -> CustomerCognitoAppClientId
#
# A pool that doesn't exist yet (e.g. prod, before it's provisioned) is
# skipped with a warning rather than failing the whole run.
#
# Any KEY=VALUE arguments update samconfig.toml the same way, and win over
# an auto-discovered value for the same key — use these for anything not
# covered by the Cognito lookup above (table names, stack_name, region, …),
# or to override the lookup if your naming convention differs.
#
# Example:
#   ./update-samconfig.sh dev
#   ./update-samconfig.sh --dry-run prod
#   ./update-samconfig.sh dev EventBusName=chonkychonk-bus-2
#
# Validates the result is still parseable TOML before writing it — the
# original file is left untouched if anything goes wrong.
# ==============================================================================

die()  { echo "[ERROR] $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/samconfig.toml"

ENVIRONMENT=""
REGION_ARG=""
DRY_RUN=false
declare -a UPDATES=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region=*)  REGION_ARG="${1#*=}"; shift ;;
    --region)    [[ $# -ge 2 ]] || die "$1 requires a value."; REGION_ARG="$2"; shift 2 ;;
    --dry-run)   DRY_RUN=true; shift ;;
    -h|--help)
      echo "Usage: $0 [--dry-run] [--region REGION] <environment> [KEY=VALUE ...]"
      exit 0
      ;;
    *=*)
      UPDATES+=("$1")
      shift
      ;;
    *)
      [[ -z "$ENVIRONMENT" ]] || die "Unexpected argument: '$1' (environment already set to '$ENVIRONMENT')."
      ENVIRONMENT="$1"
      shift
      ;;
  esac
done

[[ -n "$ENVIRONMENT" ]] || die "Usage: $0 [--dry-run] [--region REGION] <environment> [KEY=VALUE ...]"
[[ -f "$CONFIG_FILE" ]] || die "samconfig.toml not found at $CONFIG_FILE."
command -v aws >/dev/null 2>&1 || die "aws CLI not found on PATH."

python3 "$SCRIPT_DIR/update_samconfig.py" "$CONFIG_FILE" "$ENVIRONMENT" "$REGION_ARG" "$DRY_RUN" "${UPDATES[@]}"
