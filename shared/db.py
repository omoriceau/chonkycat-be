"""
Database helper for local development.
Uses pymysql to connect directly to MySQL for local testing.
"""

import os
import pymysql
from typing import Any, Optional


class LocalMySQLClient:
    """Wrapper to mimic RDS Data API but use direct MySQL connection."""

    def __init__(self):
        self.connection = None
        self._connect()

    def _connect(self):
        """Connect to local MySQL database."""
        try:
            self.connection = pymysql.connect(
                host=os.environ.get("DB_HOST", "localhost"),
                port=int(os.environ.get("DB_PORT", "3306")),
                user=os.environ.get("DB_USER", "chonky_user"),
                password=os.environ.get("DB_PASSWORD", "chonky_password"),
                database=os.environ.get("DB_NAME", "chonkychonk"),
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
            )
            print("[DB] Connected to local MySQL")
        except Exception as e:
            print(f"[DB] Connection error: {e}")
            raise

    def execute_statement(
        self,
        resourceArn: str = "",
        secretArn: str = "",
        database: str = "",
        sql: str = "",
        parameters: list = None,
        **kwargs
    ) -> dict:
        """
        Execute SQL and return results in RDS Data API format.
        """
        if parameters is None:
            parameters = []

        try:
            with self.connection.cursor() as cursor:
                # Convert RDS parameter format to pymysql format
                sql_formatted = sql
                param_values = []

                for param in parameters:
                    name = param["name"]
                    value = self._extract_value(param["value"])
                    param_values.append(value)
                    # Replace :name with %s for pymysql
                    sql_formatted = sql_formatted.replace(f":{name}", "%s")

                print(f"[DB] Executing: {sql_formatted[:100]} with {len(param_values)} params")

                cursor.execute(sql_formatted, param_values)
                self.connection.commit()

                rows = cursor.fetchall()

                # Convert to RDS Data API format
                return self._format_response(rows, cursor.description)

        except Exception as e:
            print(f"[DB] Query error: {e}")
            raise

    @staticmethod
    def _extract_value(value_dict: dict) -> Any:
        """Extract the actual value from RDS parameter format."""
        if isinstance(value_dict, dict):
            if "isNull" in value_dict and value_dict["isNull"]:
                return None
            elif "stringValue" in value_dict:
                return value_dict["stringValue"]
            elif "longValue" in value_dict:
                return value_dict["longValue"]
            elif "doubleValue" in value_dict:
                return value_dict["doubleValue"]
            elif "booleanValue" in value_dict:
                return value_dict["booleanValue"]
        return value_dict

    @staticmethod
    def _format_response(rows: list, description: Any) -> dict:
        """Convert pymysql results to RDS Data API format."""
        if not description:
            return {"records": [], "columnMetadata": []}

        column_names = [desc[0] for desc in description]
        column_metadata = [{"name": name, "type": "VARCHAR"} for name in column_names]

        records = []
        for row in rows:
            record = []
            for col_name in column_names:
                value = row.get(col_name)
                if value is None:
                    record.append({"isNull": True})
                elif isinstance(value, (int, bool)):
                    record.append({"longValue": int(value)})
                elif isinstance(value, float):
                    record.append({"doubleValue": value})
                else:
                    record.append({"stringValue": str(value)})
            records.append(record)

        return {
            "records": records,
            "columnMetadata": column_metadata,
        }

    def close(self):
        """Close the database connection."""
        if self.connection:
            self.connection.close()


def get_db_client():
    """
    Get database client (local MySQL or real RDS).
    Uses local MySQL if DB_HOST is set to localhost, 127.0.0.1, or chonkychonk-db (Docker container).
    """
    db_host = os.environ.get("DB_HOST", "")

    if db_host in ("localhost", "127.0.0.1", "host.docker.internal", "chonkychonk-db") or os.environ.get("LOCAL_MOCK_DB", "").lower() == "true":
        print(f"[DB] Using local MySQL client (host: {db_host})")
        return LocalMySQLClient()
    else:
        # Use real RDS Data API
        import boto3
        print("[DB] Using AWS RDS Data API client")
        return boto3.client("rds-data")
