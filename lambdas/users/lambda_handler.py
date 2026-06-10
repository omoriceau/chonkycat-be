"""
Lambda: GET /users/{id}

Authorization (Cognito — enforced once pool is configured):
  - Admin  : can fetch any user profile
  - Customer: can only fetch their own profile (id must match their token sub)
  - Guest  : same restriction as customer

Cognito integration is stubbed — see COGNITO_ENABLED env var.
When disabled, a mock identity can be injected via X-Dev-User-Id and
X-Dev-User-Role headers (never expose this in production).

Environment Variables:
  - DB_CLUSTER_ARN     Aurora cluster ARN
  - DB_SECRET_ARN      Secrets Manager secret ARN
  - DB_NAME            Database name (chonkychonk)
  - COGNITO_ENABLED    Set to "true" once Cognito is wired up (default: false)
  - COGNITO_USER_POOL_ID   (required when COGNITO_ENABLED=true)
  - COGNITO_APP_CLIENT_ID  (required when COGNITO_ENABLED=true)

Expected Cognito token claims (standard + custom):
  - sub               → maps to users.id  (store Cognito sub in users table,
                         or map via email claim — see note below)
  - email             → used as fallback identity lookup
  - cognito:groups    → list; membership in "admin" group grants admin role
"""

import json
import os
import boto3
from botocore.exceptions import ClientError

rds = boto3.client("rds-data")

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

COGNITO_ENABLED = os.environ.get("COGNITO_ENABLED", "false").strip().lower() == "true"


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def ok(body: dict, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def err(message: str, status: int = 400) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}),
    }


def rows_to_dicts(column_metadata: list, records: list) -> list[dict]:
    columns = [col["name"] for col in column_metadata]
    result = []
    for record in records:
        row = {}
        for col, field in zip(columns, record):
            value = next(iter(field.values())) if field != {"isNull": True} else None
            row[col] = value
        result.append(row)
    return result


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------

class Identity:
    """Resolved caller identity from Cognito (or dev stub)."""
    def __init__(self, user_id: int | None, role: str):
        self.user_id = user_id   # chonkychonk users.id  (None if unresolvable)
        self.role    = role      # "admin" | "customer" | "guest"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def resolve_identity_cognito(event: dict) -> Identity | None:
    """
    Extract and validate the Cognito JWT passed by API Gateway.

    When API Gateway is configured with a Cognito Authorizer, the validated
    claims are injected into event["requestContext"]["authorizer"]["claims"].
    No manual JWT verification is needed in that setup.

    If you're using a Lambda Authorizer instead, the claims land in
    event["requestContext"]["authorizer"] directly — adjust the path below.

    Returns None if claims are absent or malformed (treat as 401).
    """
    try:
        claims = event["requestContext"]["authorizer"]["claims"]
    except (KeyError, TypeError):
        return None

    email  = claims.get("email")
    groups = claims.get("cognito:groups", "")  # comma-separated string from API GW
    if isinstance(groups, str):
        groups = [g.strip() for g in groups.split(",") if g.strip()]

    role = "admin" if "admin" in groups else claims.get("custom:role", "customer")

    # Map Cognito identity → internal user id via email.
    # NOTE: If you store the Cognito sub in users.cognito_sub, switch to that
    # for a more robust mapping (email can change).
    return Identity(user_id=None, role=role), email  # caller must resolve id from email


def resolve_identity_dev(event: dict) -> Identity:
    """
    DEV ONLY stub — reads identity from custom headers.
    Never reachable when COGNITO_ENABLED=true.
    """
    headers    = event.get("headers") or {}
    user_id    = headers.get("X-Dev-User-Id")
    role       = headers.get("X-Dev-User-Role", "customer").lower()

    try:
        user_id = int(user_id) if user_id else None
    except ValueError:
        user_id = None

    return Identity(user_id=user_id, role=role)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def fetch_user_by_id(user_id: int) -> dict | None:
    sql = """
        SELECT
            u.id,
            u.email,
            u.first_name,
            u.last_name,
            u.phone,
            u.role,
            u.status,
            u.created_at,
            u.updated_at
        FROM users u
        WHERE u.id = :user_id
        LIMIT 1
    """
    try:
        resp = rds.execute_statement(
            resourceArn=DB_CLUSTER_ARN,
            secretArn=DB_SECRET_ARN,
            database=DB_NAME,
            sql=sql,
            parameters=[{"name": "user_id", "value": {"longValue": user_id}}],
            includeResultMetadata=True,
        )
    except ClientError as e:
        print(f"RDS error (fetch_user_by_id): {e}")
        raise

    rows = rows_to_dicts(resp["columnMetadata"], resp["records"])
    return rows[0] if rows else None


def fetch_user_by_email(email: str) -> dict | None:
    sql = """
        SELECT
            u.id,
            u.email,
            u.first_name,
            u.last_name,
            u.phone,
            u.role,
            u.status,
            u.created_at,
            u.updated_at
        FROM users u
        WHERE u.email = :email
        LIMIT 1
    """
    try:
        resp = rds.execute_statement(
            resourceArn=DB_CLUSTER_ARN,
            secretArn=DB_SECRET_ARN,
            database=DB_NAME,
            sql=sql,
            parameters=[{"name": "email", "value": {"stringValue": email}}],
            includeResultMetadata=True,
        )
    except ClientError as e:
        print(f"RDS error (fetch_user_by_email): {e}")
        raise

    rows = rows_to_dicts(resp["columnMetadata"], resp["records"])
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:

    # -- Resolve caller identity ---------------------------------------------
    caller_email = None

    if COGNITO_ENABLED:
        result = resolve_identity_cognito(event)
        if result is None:
            return err("Unauthorized", status=401)
        identity, caller_email = result
    else:
        # Dev stub — log a warning so it's visible in CloudWatch
        print("WARNING: COGNITO_ENABLED=false — running in dev mode, no auth enforced")
        identity = resolve_identity_dev(event)

    # -- Parse requested user id from path -----------------------------------
    path_params  = event.get("pathParameters") or {}
    requested_id = path_params.get("id")

    if not requested_id:
        return err("Missing path parameter: id", status=400)

    try:
        requested_id = int(requested_id)
    except ValueError:
        return err("Path parameter 'id' must be an integer", status=400)

    # -- Resolve caller's internal id (Cognito path) -------------------------
    if COGNITO_ENABLED and not identity.is_admin:
        # Resolve caller's DB id from their email claim so we can enforce ownership
        if not caller_email:
            return err("Token is missing email claim", status=401)
        caller_record = fetch_user_by_email(caller_email)
        if not caller_record:
            return err("Authenticated user not found in system", status=404)
        identity.user_id = caller_record["id"]

    # -- Authorization check -------------------------------------------------
    if not identity.is_admin:
        if identity.user_id is None:
            # Dev stub with no user id provided — reject
            return err("Unauthorized", status=401)
        if identity.user_id != requested_id:
            # Return 404 instead of 403 to avoid leaking whether the id exists
            return err("User not found", status=404)

    # -- Fetch target user ---------------------------------------------------
    try:
        user = fetch_user_by_id(requested_id)
    except ClientError:
        return err("Database error", status=500)

    if not user:
        return err("User not found", status=404)

    return ok({"data": user})


# ---------------------------------------------------------------------------
# User registration helper
# Call this after creating a new user in the DB to trigger the welcome email.
# ---------------------------------------------------------------------------

import json as _json
import os as _os
import boto3 as _boto3
from shared.events import SOURCE_USERS, USER_REGISTERED

_EVENT_BUS  = _os.environ.get("EVENT_BUS_NAME", "chonkychonk-bus")
_eb         = _boto3.client("events")


def emit_user_registered(user_id: int, email: str,
                         first_name: str | None, last_name: str | None) -> None:
    """
    Emit UserRegistered → Email Lambda sends welcome email.
    Call this immediately after a new user row is inserted.
    """
    try:
        _eb.put_events(Entries=[{
            "Source":       SOURCE_USERS,
            "DetailType":   USER_REGISTERED,
            "Detail":       _json.dumps({
                "user_id":    user_id,
                "email":      email,
                "first_name": first_name,
                "last_name":  last_name,
            }),
            "EventBusName": _EVENT_BUS,
        }])
        logger.info("UserRegistered emitted | user=%s", user_id)
    except Exception as e:
        logger.error("Failed to emit UserRegistered: %s", e)
