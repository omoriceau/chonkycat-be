"""
users/service.py

UserService handles:
  - Create / read / update / delete of users in DynamoDB
  - Email uniqueness enforcement (via the lock-item pattern in db.py —
    mirrors the UNIQUE constraint Postgres used to give us for free)
  - Pagination for the list endpoint
  - EventBridge: UserCreated (-> Email Lambda, sends a welcome email)
"""

import json
import logging
import os
import uuid
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from models import (
    CreateUserRequest,
    UpdateUserRequest,
    ValidationError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# EventBridge
#
# NOTE: the orders Lambda pulls these from a shared `shared.events` layer
# (SOURCE_ORDERS / ORDER_CREATED / ...). That layer wasn't included in what
# was shared with me, so these are defined locally for now — move them into
# the shared layer if/when you want a single source of truth across lambdas.
# ---------------------------------------------------------------------------
SOURCE_USERS = "chonkychonk.users"
USER_CREATED = "UserCreated"

EVENTBRIDGE_BUS = os.environ.get("EVENT_BUS_NAME", "chonkychonk-bus")

# DynamoDB has no auto-increment PK like the old `id SERIAL`. Every user now
# gets a random UUID as its user_id — this is a visible API change for any
# caller that assumed integer, sequential user IDs.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


class UserService:

    def __init__(self, db_client=None, events_client=None):
        from db import get_db_client, EmailAlreadyExists, now_iso
        self._db = db_client or get_db_client()
        self._events = events_client or boto3.client("events")
        self._EmailAlreadyExists = EmailAlreadyExists
        self._now_iso = now_iso

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def get_user(self, user_id: str) -> Optional[dict]:
        item = self._db.get_user(user_id)
        return self._to_response(item) if item else None

    def list_users(self, limit: int = 50, offset: int = 0,
                    role: Optional[str] = None,
                    status: Optional[str] = None) -> dict:
        """
        Paginated user listing, optionally filtered by role/status.

        NOTE: DynamoDB has no OFFSET/LIMIT like SQL. To keep the existing
        limit/offset API contract, this pulls every matching item (Scan,
        filtered server-side by role/status), sorts by created_at ascending
        (the closest equivalent to the old "ORDER BY id ASC" — since ids
        are now random UUIDs, not sequential, they carry no useful order),
        then slices in memory. Fine for a user base up to the low tens of
        thousands; if it grows much larger, switch this endpoint to
        cursor-based (LastEvaluatedKey) pagination instead.
        """
        limit = max(1, min(limit, _MAX_LIMIT))
        offset = max(0, offset)

        items = self._db.list_users(role=role, status=status)
        items.sort(key=lambda i: i.get("created_at", ""))

        page = items[offset:offset + limit]

        return {
            "users":  [self._to_response(i) for i in page],
            "limit":  limit,
            "offset": offset,
            "count":  len(page),
        }

    def create_user(self, request: CreateUserRequest) -> dict:
        user_id = str(uuid.uuid4())
        created_at = self._now_iso()

        user_item = {
            "user_id": user_id,
            "email": request.email,
            "first_name": request.first_name,
            "last_name": request.last_name,
            "phone": request.phone,
            "role": request.role,
            "status": request.status,
            "created_at": created_at,
            "updated_at": created_at,
        }
        # DynamoDB item can't store `None` values in the way we want to read
        # them back consistently — drop unset optional fields entirely.
        user_item = {k: v for k, v in user_item.items() if v is not None}

        try:
            self._db.create_user(user_item)
        except self._EmailAlreadyExists:
            raise ValidationError(f"A user with email '{request.email}' already exists")

        user = self._to_response(user_item)
        logger.info("User created | email=%s", request.email)

        self._emit_user_created(user)

        return user

    def update_user(self, user_id: str, update: UpdateUserRequest) -> Optional[dict]:
        # Confirm the user exists first
        existing = self._db.get_user(user_id)
        if existing is None:
            return None

        updates = {}
        for column, value in (
            ("first_name", update.first_name),
            ("last_name", update.last_name),
            ("phone", update.phone),
            ("role", update.role),
            ("status", update.status),
        ):
            if value is not None:
                updates[column] = value

        new_email = update.email if update.email is not None else None

        try:
            item = self._db.update_user(
                user_id,
                updates,
                current_email=existing["email"],
                new_email=new_email,
            )
        except self._EmailAlreadyExists:
            raise ValidationError(f"A user with email '{update.email}' already exists")

        logger.info("User updated | user_id=%s", user_id)
        return self._to_response(item)

    def delete_user(self, user_id: str) -> bool:
        """
        Hard delete — matches the old RDS behaviour (no soft-delete column).
        """
        existing = self._db.get_user(user_id)
        if existing is None:
            return False

        deleted = self._db.delete_user(user_id, email=existing["email"])
        if deleted:
            logger.info("User deleted | user_id=%s", user_id)
        return deleted

    # ------------------------------------------------------------------
    # EventBridge
    # ------------------------------------------------------------------

    def _emit_user_created(self, user: dict) -> None:
        """
        Fires a UserCreated event. The Email Lambda listens on this bus
        filtered to source=chonkychonk.users, detail-type=UserCreated,
        and sends the new user a welcome email.
        """
        detail = {
            "user_id":    user["id"],
            "email":      user["email"],
            "first_name": user["first_name"],
            "last_name":  user["last_name"],
            "role":       user["role"],
        }
        try:
            self._events.put_events(Entries=[{
                "Source":       SOURCE_USERS,
                "DetailType":   USER_CREATED,
                "Detail":       json.dumps(detail),
                "EventBusName": EVENTBRIDGE_BUS,
            }])
            logger.info("EventBridge UserCreated emitted | user_id=%s", user["id"])
        except ClientError as e:
            # Log but don't fail user creation — a dead-letter / retry policy
            # on EventBridge should handle redelivery
            logger.error("Failed to emit UserCreated event: %s", e)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_response(item: dict) -> dict:
        return {
            "id":         item.get("user_id"),
            "email":      item.get("email"),
            "first_name": item.get("first_name"),
            "last_name":  item.get("last_name"),
            "phone":      item.get("phone"),
            "role":       item.get("role"),
            "status":     item.get("status"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        }
