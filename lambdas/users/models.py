"""
users/models.py

Typed request/response models for the users Lambda.
Validated on entry before any DB interaction.
"""

import re
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    pass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ALLOWED_ROLES   = {"customer", "admin", "staff"}
ALLOWED_STATUSES = {"active", "inactive", "suspended"}

# Mirrors the Cognito User Pool's password policy (chonkychonk-admin). Kept
# in sync manually — if the pool policy changes, update this too so bad
# passwords are rejected here instead of round-tripping to Cognito first.
PASSWORD_MIN_LENGTH = 12
PASSWORD_RE = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^\w\s]).{%d,}$" % PASSWORD_MIN_LENGTH
)


# ---------------------------------------------------------------------------
# Inbound — what the frontend sends
# ---------------------------------------------------------------------------

@dataclass
class CreateUserRequest:
    email:      str
    password:   str
    first_name: Optional[str] = None
    last_name:  Optional[str] = None
    phone:      Optional[str] = None
    role:       str = "customer"
    status:     str = "active"


@dataclass
class UpdateUserRequest:
    email:      Optional[str] = None
    first_name: Optional[str] = None
    last_name:  Optional[str] = None
    phone:      Optional[str] = None
    role:       Optional[str] = None
    status:     Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require(d: dict, key: str):
    val = d.get(key)
    if val is None or (isinstance(val, str) and val.strip() == ""):
        raise ValidationError(f"Missing required field: '{key}'")
    return val


def _validate_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValidationError(f"'{email}' is not a valid email address")
    return email


def _validate_role(role: str) -> str:
    role = role.strip().lower()
    if role not in ALLOWED_ROLES:
        raise ValidationError(f"'role' must be one of {sorted(ALLOWED_ROLES)}")
    return role


def _validate_status(status: str) -> str:
    status = status.strip().lower()
    if status not in ALLOWED_STATUSES:
        raise ValidationError(f"'status' must be one of {sorted(ALLOWED_STATUSES)}")
    return status


def _validate_password(password: str) -> str:
    if not PASSWORD_RE.match(password):
        raise ValidationError(
            f"'password' must be at least {PASSWORD_MIN_LENGTH} characters and "
            "include an uppercase letter, a lowercase letter, a number, and a symbol"
        )
    return password


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_create_user_request(data: dict) -> CreateUserRequest:
    email    = _validate_email(str(_require(data, "email")))
    password = _validate_password(str(_require(data, "password")))

    role   = _validate_role(str(data.get("role", "customer")))
    status = _validate_status(str(data.get("status", "active")))

    return CreateUserRequest(
        email      = email,
        password   = password,
        first_name = data.get("first_name"),
        last_name  = data.get("last_name"),
        phone      = data.get("phone"),
        role       = role,
        status     = status,
    )


def parse_update_user_request(data: dict) -> UpdateUserRequest:
    """
    Allows partial updates: any subset of email, first_name, last_name,
    phone, role, status.
    """
    if not data:
        raise ValidationError(
            "At least one field must be provided for update "
            "(email, first_name, last_name, phone, role, status)"
        )

    update = UpdateUserRequest()

    if "email" in data:
        update.email = _validate_email(str(_require(data, "email")))
    if "first_name" in data:
        update.first_name = data.get("first_name")
    if "last_name" in data:
        update.last_name = data.get("last_name")
    if "phone" in data:
        update.phone = data.get("phone")
    if "role" in data:
        update.role = _validate_role(str(_require(data, "role")))
    if "status" in data:
        update.status = _validate_status(str(_require(data, "status")))

    return update
