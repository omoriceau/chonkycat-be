"""
users/service.py

UserService handles:
  - Create / read / update / delete of public.users rows
  - Email uniqueness enforcement (mirrors the UNIQUE constraint in Postgres)
  - Pagination for list endpoint
"""

import logging
from typing import Optional

import psycopg2.errors
from botocore.exceptions import ClientError

from models import (
    CreateUserRequest,
    UpdateUserRequest,
    ValidationError,
)

logger = logging.getLogger(__name__)

# Columns returned to the client — never expose internal-only columns here
_USER_COLUMNS = (
    "id, email, first_name, last_name, phone, role, status, "
    "created_at, updated_at"
)


class UserService:

    def __init__(self, db_client=None):
        from db import PostgreSQLClient
        self._db = db_client or PostgreSQLClient()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def get_user(self, user_id: int) -> Optional[dict]:
        sql = f"SELECT {_USER_COLUMNS} FROM users WHERE id = $1"
        resp = self._execute(
            sql,
            [{"name": "user_id", "value": {"longValue": user_id}}],
            "get_user",
            include_metadata=True,
        )
        rows = self._to_dicts(resp["columnMetadata"], resp["records"])
        return self._to_response(rows[0]) if rows else None

    def list_users(self, limit: int = 50, offset: int = 0,
                    role: Optional[str] = None,
                    status: Optional[str] = None) -> dict:
        """
        Paginated user listing, optionally filtered by role/status.
        """
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        where_clauses = []
        params = []
        idx = 1

        if role:
            where_clauses.append(f"role = ${idx}")
            params.append({"name": "role", "value": {"stringValue": role}})
            idx += 1
        if status:
            where_clauses.append(f"status = ${idx}")
            params.append({"name": "status", "value": {"stringValue": status}})
            idx += 1

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        sql = f"""
            SELECT {_USER_COLUMNS}
            FROM   users
            {where_sql}
            ORDER BY id ASC
            LIMIT  ${idx} OFFSET ${idx + 1}
        """
        params.append({"name": "limit", "value": {"longValue": limit}})
        params.append({"name": "offset", "value": {"longValue": offset}})

        resp = self._execute(sql, params, "list_users", include_metadata=True)
        rows = self._to_dicts(resp["columnMetadata"], resp["records"])

        return {
            "users":  [self._to_response(r) for r in rows],
            "limit":  limit,
            "offset": offset,
            "count":  len(rows),
        }

    def create_user(self, request: CreateUserRequest) -> dict:
        sql = """
            INSERT INTO users (email, first_name, last_name, phone, role, status)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, email, first_name, last_name, phone, role, status,
                      created_at, updated_at
        """
        params = [
            {"name": "email",      "value": {"stringValue": request.email}},
            {"name": "first_name", "value": {"stringValue": request.first_name} if request.first_name else {"isNull": True}},
            {"name": "last_name",  "value": {"stringValue": request.last_name} if request.last_name else {"isNull": True}},
            {"name": "phone",      "value": {"stringValue": request.phone} if request.phone else {"isNull": True}},
            {"name": "role",       "value": {"stringValue": request.role}},
            {"name": "status",     "value": {"stringValue": request.status}},
        ]

        try:
            resp = self._execute(sql, params, "create_user", include_metadata=True)
        except psycopg2.errors.UniqueViolation:
            raise ValidationError(f"A user with email '{request.email}' already exists")

        rows = self._to_dicts(resp["columnMetadata"], resp["records"])
        logger.info("User created | email=%s", request.email)
        return self._to_response(rows[0])

    def update_user(self, user_id: int, update: UpdateUserRequest) -> Optional[dict]:
        # Confirm the user exists first
        existing = self.get_user(user_id)
        if existing is None:
            return None

        fields = []
        params = []
        idx = 1

        for column, value in (
            ("email", update.email),
            ("first_name", update.first_name),
            ("last_name", update.last_name),
            ("phone", update.phone),
            ("role", update.role),
            ("status", update.status),
        ):
            if value is not None:
                fields.append(f"{column} = ${idx}")
                params.append({"name": column, "value": {"stringValue": value}})
                idx += 1

        fields.append("updated_at = CURRENT_TIMESTAMP")

        sql = f"""
            UPDATE users
            SET    {', '.join(fields)}
            WHERE  id = ${idx}
            RETURNING id, email, first_name, last_name, phone, role, status,
                      created_at, updated_at
        """
        params.append({"name": "user_id", "value": {"longValue": user_id}})

        try:
            resp = self._execute(sql, params, "update_user", include_metadata=True)
        except psycopg2.errors.UniqueViolation:
            raise ValidationError(f"A user with email '{update.email}' already exists")

        rows = self._to_dicts(resp["columnMetadata"], resp["records"])
        logger.info("User updated | user_id=%s", user_id)
        return self._to_response(rows[0])

    def delete_user(self, user_id: int) -> bool:
        """
        Hard delete — the users table has no deleted_at column.
        If soft-delete is preferred, switch this to `UPDATE users SET status='inactive'`.
        """
        sql = "DELETE FROM users WHERE id = $1 RETURNING id"
        resp = self._execute(
            sql,
            [{"name": "user_id", "value": {"longValue": user_id}}],
            "delete_user",
            include_metadata=True,
        )
        rows = self._to_dicts(resp["columnMetadata"], resp["records"])
        if rows:
            logger.info("User deleted | user_id=%s", user_id)
            return True
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _execute(self, sql: str, params: list, label: str, include_metadata: bool = False) -> dict:
        try:
            return self._db.execute_statement(
                sql=sql,
                parameters=params,
                includeResultMetadata=include_metadata,
            )
        except psycopg2.errors.UniqueViolation:
            raise
        except Exception as e:
            logger.error("DB error [%s]: %s", label, e)
            raise

    @staticmethod
    def _to_dicts(column_metadata: list, records: list) -> list[dict]:
        columns = [col["name"] for col in column_metadata]
        result = []
        for record in records:
            row = {}
            for col, field in zip(columns, record):
                row[col] = next(iter(field.values())) if field != {"isNull": True} else None
            result.append(row)
        return result

    @staticmethod
    def _to_response(row: dict) -> dict:
        return {
            "id":         row["id"],
            "email":      row["email"],
            "first_name": row["first_name"],
            "last_name":  row["last_name"],
            "phone":      row["phone"],
            "role":       row["role"],
            "status":     row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
