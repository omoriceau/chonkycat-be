"""
Simple PostgreSQL helper for AWS Lambda / local dev.
Uses psycopg2 directly.
"""

import os
import psycopg2
import psycopg2.extras
from typing import Optional, Any


class PostgreSQLClient:
    def __init__(self):
        self.connection = None
        self._connect()

    def _connect(self):
        db_host = os.environ.get("DB_HOST", "localhost")
        db_port = int(os.environ.get("DB_PORT", "5432"))
        db_user = os.environ.get("DB_USER", "chonky_admin")
        db_password = os.environ.get("DB_PASSWORD", "")
        db_name = os.environ.get("DB_NAME", "chonky")

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