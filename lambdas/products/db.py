"""
Database helper for local and AWS RDS PostgreSQL.
Uses psycopg2 to connect directly to PostgreSQL.
Retrieves credentials from AWS Secrets Manager or environment variables.
"""

import os
import sys
import psycopg2
import psycopg2.extras
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from shared.secrets import get_db_password

class PostgreSQLClient:
    """Wrapper to provide RDS Data API-like interface using direct PostgreSQL connection."""

    def __init__(self):
        self.connection = None
        self._connect()

    def _connect(self):
        """Connect to PostgreSQL database using environment variables and AWS Secrets Manager."""
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

        print(f"[DB] Connecting to {db_host}:{db_port} db={db_name} user={db_user}")
        self.connection = psycopg2.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_password,
            database=db_name,
            connect_timeout=5,
        )
        self.connection.autocommit = True
        print(f"[DB] Connected.")

    def _ensure_connected(self):
        """Reconnect if the connection was dropped between Lambda invocations."""
        try:
            self.connection.cursor().execute("SELECT 1")
        except Exception:
            print("[DB] Connection lost — reconnecting...")
            self._connect()

    def execute_statement(
        self,
        sql: str = "",
        parameters: list = None,
        includeResultMetadata: bool = False,
        # Accept (and ignore) Aurora Data API args so call sites don't need changing
        resourceArn: str = "",
        secretArn: str = "",
        database: str = "",
        **kwargs,
    ) -> dict:
        """
        Execute SQL and return results in RDS Data API format.

        Parameters must use $1, $2 ... placeholders (PostgreSQL native style).
        The `parameters` list is ordered — names are ignored, only values matter.
        """
        if parameters is None:
            parameters = []

        self._ensure_connected()

        # Extract ordered values from the RDS-style parameter list.
        # The lambda builds params in the same order as the $1/$2 placeholders.
        param_values = [self._extract_value(p["value"]) for p in parameters]

        # psycopg2 uses %s placeholders — replace $1, $2, ... with %s in order.
        import re
        sql_formatted = re.sub(r"\$\d+", "%s", sql)

        print(f"[DB] SQL: {sql_formatted.strip()[:200]}")
        print(f"[DB] Params ({len(param_values)}): {param_values}")

        try:
            with self.connection.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            ) as cursor:
                cursor.execute(sql_formatted, param_values)
                rows = cursor.fetchall()
                description = cursor.description
        except Exception as e:
            print(f"[DB] Query error: {e}")
            raise

        return self._format_response(rows, description, includeResultMetadata)

    @staticmethod
    def _extract_value(value_dict: dict) -> Any:
        """Extract the actual value from RDS parameter format."""
        if not isinstance(value_dict, dict):
            return value_dict
        if value_dict.get("isNull"):
            return None
        for key in ("stringValue", "longValue", "doubleValue", "booleanValue"):
            if key in value_dict:
                return value_dict[key]
        return None

    @staticmethod
    def _format_response(rows, description, includeResultMetadata: bool = False) -> dict:
        """Convert psycopg2 results to RDS Data API format."""
        if not description:
            return {"records": [], "columnMetadata": []}

        column_names = [desc.name for desc in description]

        records = []
        for row in rows:
            record = []
            for col in column_names:
                value = row[col]
                if value is None:
                    record.append({"isNull": True})
                elif isinstance(value, bool):
                    record.append({"booleanValue": value})
                elif isinstance(value, int):
                    record.append({"longValue": value})
                elif isinstance(value, float):
                    record.append({"doubleValue": value})
                else:
                    record.append({"stringValue": str(value)})
            records.append(record)

        result = {"records": records}
        if includeResultMetadata:
            result["columnMetadata"] = [{"name": n} for n in column_names]
        return result

    def close(self):
        if self.connection:
            self.connection.close()


def get_db_client() -> PostgreSQLClient:
    return PostgreSQLClient()