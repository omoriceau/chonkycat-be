"""
Simple PostgreSQL helper for AWS Lambda / local dev.
Uses psycopg2 directly.
Retrieves credentials from AWS Secrets Manager or environment variables.
"""

import os
import sys
import psycopg2
import psycopg2.extras
from typing import Optional, Any

# Import local secrets module, not built-in
from secrets import get_db_password


class PostgreSQLClient:
    def __init__(self):
        self.connection = None
        self._connect()

    def _connect(self):
        # Environment variables MUST be set by Lambda configuration
        db_host = os.environ.get("DB_HOST")
        db_port_str = os.environ.get("DB_PORT")
        db_user = os.environ.get("DB_USER")
        db_name = os.environ.get("DB_NAME")

        # Validate required connection parameters
        missing_vars = []
        if not db_host:
            missing_vars.append("DB_HOST")
        if not db_port_str:
            missing_vars.append("DB_PORT")
        if not db_user:
            missing_vars.append("DB_USER")
        if not db_name:
            missing_vars.append("DB_NAME")

        if missing_vars:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing_vars)}. "
                f"Ensure the Lambda function is configured with these environment variables via SAM template or AWS console."
            )

        # Retrieve password from AWS Secrets Manager (with fallback to env var)
        db_password = get_db_password()

        db_port = int(db_port_str)

        self.connection = psycopg2.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_password,
            database=db_name,
            connect_timeout=5,
        )
        self.connection.autocommit = True

    def _ensure_connected(self):
        try:
            with self.connection.cursor() as cur:
                cur.execute("SELECT 1")
        except Exception:
            self._connect()

    # ------------------------------------------------------------------
    # QUERY HELPERS
    # ------------------------------------------------------------------

    def fetch_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        self._ensure_connected()

        with self.connection.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        self._ensure_connected()

        with self.connection.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._ensure_connected()

        with self.connection.cursor() as cur:
            cur.execute(sql, params)

    # ------------------------------------------------------------------
    # CLEAN CLOSE
    # ------------------------------------------------------------------

    def close(self):
        if self.connection:
            self.connection.close()


def get_db_client() -> PostgreSQLClient:
    return PostgreSQLClient()
