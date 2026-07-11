"""
users/lambda_handler.py

Entry point for the Users API.

Routes:
  GET    /users            List users (supports ?limit=&offset=&role=&status=)
  GET    /users/{userId}   Fetch a single user
  POST   /users            Create a user
  PUT    /users/{userId}   Update a user (partial)
  DELETE /users/{userId}   Delete a user

Environment Variables:
  - USERS_TABLE_NAME     DynamoDB table name (aws_dynamodb_table.users.name)
  - COGNITO_USER_POOL_ID Cognito User Pool backing auth (chonkychonk-admin)
  - EVENT_BUS_NAME       EventBridge bus for the UserCreated event (optional,
                          defaults to "chonkychonk-bus")

NOTE: user IDs used to be sequential integers (Postgres SERIAL). They are
now randomly generated UUID strings, since DynamoDB has no auto-increment
primary key. Any client that parsed userId as an int needs to change to
treat it as an opaque string.

Example create request body:
{
    "email": "benny.garcia@email.com",
    "password": "Correct-Horse-Battery-9",
    "first_name": "Benny",
    "last_name": "Garcia",
    "phone": "+1-416-555-0142",
    "role": "customer",
    "status": "active"
}

Auth: user creation is backed by Cognito (chonkychonk-admin user pool). The
Lambda calls AdminCreateUser + AdminSetUserPassword itself, so the caller
just supplies a password once — no confirmation code / temp-password reset
flow. The DynamoDB user_id is the Cognito `sub`, so the two records are
always linked 1:1.
"""

import json
import logging

from models import ValidationError, parse_create_user_request, parse_update_user_request
from service import UserService
from botocore.exceptions import ClientError
from shared.cors import build_cors_headers, is_preflight, preflight_response

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Module-level service — reused across warm invocations
# Lazy-initialized to avoid runtime crash if DB connection fails on cold start
_service = None


def _get_service() -> UserService:
    global _service
    if _service is None:
        _service = UserService()
    return _service


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

# Set once at the top of lambda_handler() so ok()/err() can shape CORS
# headers for the current request without threading `event` through every
# handler function.
_current_event: dict = {}


def _cors_headers() -> dict:
    return {
        "Content-Type": "application/json",
        **build_cors_headers(_current_event),
    }


def ok(body: dict, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": _cors_headers(),
        "body": json.dumps(body, default=str),
    }


def err(message: str, status: int = 400) -> dict:
    return {
        "statusCode": status,
        "headers": _cors_headers(),
        "body": json.dumps({"error": message}),
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    logger.info("User request received")

    global _current_event
    _current_event = event or {}

    if is_preflight(event):
        return preflight_response(event)

    method = event.get("httpMethod", "")
    has_path_id = bool((event.get("pathParameters") or {}).get("userId"))

    if method == "GET":
        return _handle_get_user(event) if has_path_id else _handle_list_users(event)
    elif method == "POST":
        return _handle_create_user(event)
    elif method == "PUT":
        return _handle_update_user(event)
    elif method == "DELETE":
        return _handle_delete_user(event)
    else:
        return err(f"Unsupported HTTP method: {method}", status=405)


def _parse_user_id(event: dict) -> str:
    """user_id is now a UUID string, not an int — just validate it's present."""
    user_id = event["pathParameters"]["userId"]
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("empty userId")
    return user_id


def _handle_get_user(event: dict) -> dict:
    """GET /users/{userId}"""
    try:
        user_id = _parse_user_id(event)
    except (KeyError, TypeError, ValueError):
        return err("Invalid userId in path", status=400)

    try:
        result = _get_service().get_user(user_id)
        if result is None:
            return err("User not found", status=404)
        return ok({"user": result})
    except Exception:
        logger.exception("Error retrieving user")
        return err("Internal server error", status=500)


def _handle_list_users(event: dict) -> dict:
    """GET /users?limit=&offset=&role=&status="""
    params = event.get("queryStringParameters") or {}

    try:
        limit = int(params.get("limit", 50))
        offset = int(params.get("offset", 0))
    except ValueError:
        return err("'limit' and 'offset' must be integers", status=400)

    role = params.get("role")
    status = params.get("status")

    try:
        result = _get_service().list_users(limit=limit, offset=offset, role=role, status=status)
        return ok(result)
    except Exception:
        logger.exception("Error listing users")
        return err("Internal server error", status=500)


def _handle_create_user(event: dict) -> dict:
    """POST /users"""
    body = event.get("body", "{}")
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except json.JSONDecodeError:
        return err("Request body is not valid JSON", status=400)

    try:
        request = parse_create_user_request(data)
    except ValidationError as e:
        return err(str(e), status=422)

    try:
        result = _get_service().create_user(request)
    except ValidationError as e:
        return err(str(e), status=409)
    except ClientError:
        logger.exception("Infrastructure error creating user")
        return err("Internal server error", status=500)
    except Exception:
        logger.exception("Unexpected error creating user")
        return err("Internal server error", status=500)

    return ok({"message": "User created.", "user": result}, status=201)


def _handle_update_user(event: dict) -> dict:
    """PUT /users/{userId}"""
    try:
        user_id = _parse_user_id(event)
    except (KeyError, TypeError, ValueError):
        return err("Invalid userId in path", status=400)

    body = event.get("body", "{}")
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except json.JSONDecodeError:
        return err("Request body is not valid JSON", status=400)

    try:
        update = parse_update_user_request(data)
    except ValidationError as e:
        return err(str(e), status=422)

    try:
        result = _get_service().update_user(user_id, update)
        if result is None:
            return err("User not found", status=404)
        return ok({"message": "User updated successfully", "user": result})
    except ValidationError as e:
        return err(str(e), status=409)
    except ClientError:
        logger.exception("Infrastructure error updating user")
        return err("Internal server error", status=500)
    except Exception:
        logger.exception("Unexpected error updating user")
        return err("Internal server error", status=500)


def _handle_delete_user(event: dict) -> dict:
    """DELETE /users/{userId}"""
    try:
        user_id = _parse_user_id(event)
    except (KeyError, TypeError, ValueError):
        return err("Invalid userId in path", status=400)

    try:
        success = _get_service().delete_user(user_id)
        if not success:
            return err("User not found", status=404)
        return ok({"message": "User deleted successfully", "user_id": user_id})
    except Exception:
        logger.exception("Error deleting user")
        return err("Internal server error", status=500)
