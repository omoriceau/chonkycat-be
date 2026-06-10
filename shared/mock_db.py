"""
Mock RDS Data API client for local testing.
Provides a simple in-memory database with sample data.
"""

import os
from datetime import datetime
from typing import Any


class MockRDSClient:
    """Mock boto3 RDS Data API client."""

    def __init__(self):
        self.products = self._init_products()
        self.users = self._init_users()
        self.orders = self._init_orders()

    def _init_products(self) -> list[dict]:
        """Initialize sample products matching chonky-schema."""
        return [
            {
                "id": 1,
                "sku": "CHONK-001",
                "name": "Premium Chonk Bed",
                "description": "Extra comfy bed for round cats",
                "category": "Beds",
                "price": 49.99,
                "qty": 5,
                "low_stock_threshold": 3,
                "active": 1,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": 2,
                "sku": "CHONK-002",
                "name": "Fancy Collar",
                "description": "Sparkly collar for chonky queens",
                "category": "Accessories",
                "price": 15.99,
                "qty": 20,
                "low_stock_threshold": 5,
                "active": 1,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": 3,
                "sku": "CHONK-003",
                "name": "Treat Pouch",
                "description": "Refillable pouch for treats",
                "category": "Treats",
                "price": 9.99,
                "qty": 2,
                "low_stock_threshold": 5,
                "active": 1,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": 4,
                "sku": "CHONK-004",
                "name": "Old Product",
                "description": "Inactive product",
                "category": "Misc",
                "price": 1.99,
                "qty": 100,
                "low_stock_threshold": 5,
                "active": 0,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            },
        ]

    def _init_users(self) -> list[dict]:
        """Initialize sample users matching chonky-schema."""
        return [
            {
                "id": 1,
                "email": "chonker@example.com",
                "first_name": "Chonky",
                "last_name": "Cat",
                "phone": "555-0001",
                "role": "customer",
                "status": "active",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": 2,
                "email": "fluffy@example.com",
                "first_name": "Fluffy",
                "last_name": "Kitten",
                "phone": "555-0002",
                "role": "customer",
                "status": "active",
                "created_at": "2026-01-02T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
            },
        ]

    def _init_orders(self) -> list[dict]:
        """Initialize sample orders matching chonky-schema."""
        return [
            {
                "id": 1,
                "user_id": 1,
                "status": "completed",
                "subtotal": 49.99,
                "tax_amount": 3.50,
                "shipping_amount": 5.00,
                "total_amount": 58.49,
                "customer_notes": "Leave at door",
                "shipping_name": "Chonky Cat",
                "shipping_address1": "123 Whisker Lane",
                "shipping_address2": None,
                "shipping_city": "Kitty City",
                "shipping_province": "ON",
                "shipping_postal_code": "K1A 0B1",
                "shipping_country": "Canada",
                "created_at": "2026-01-15T00:00:00Z",
                "updated_at": "2026-01-15T00:00:00Z",
            },
        ]

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
        Mock execute_statement for RDS Data API.
        Returns result in the same format as the real API.
        """
        if parameters is None:
            parameters = []

        # Parse the SQL and execute against mock data
        sql_upper = sql.strip().upper()
        
        print(f"[MOCK_DB] SQL Query: {sql_upper[:150]}")
        print(f"[MOCK_DB] Parameters: {parameters}")

        if "COUNT" in sql_upper and "PRODUCTS" in sql_upper:
            print("[MOCK_DB] Detected COUNT query")
            return self._execute_count_products(sql, parameters)
        elif "SELECT" in sql_upper and "FROM PRODUCTS" in sql_upper:
            print("[MOCK_DB] Detected SELECT products query")
            return self._execute_select_products(sql, parameters)
        elif "SELECT" in sql_upper and "FROM USERS" in sql_upper:
            print("[MOCK_DB] Detected SELECT users query")
            return self._execute_select_users(sql, parameters)
        else:
            # Return empty result for unrecognized queries
            print(f"[MOCK_DB] Unrecognized query pattern")
            return {"records": [], "columnMetadata": []}

    def _execute_count_products(self, sql: str, parameters: list) -> dict:
        """Execute COUNT query on products."""
        filtered = self._filter_products(parameters)
        total = len(filtered)
        return {
            "records": [[{"longValue": total}]],
            "columnMetadata": [{"name": "total", "type": "LONG"}],
        }

    def _execute_select_products(self, sql: str, parameters: list) -> dict:
        """Execute SELECT query on products."""
        filtered = self._filter_products(parameters)

        # Extract LIMIT and OFFSET
        limit = 100
        offset = 0
        for param in parameters:
            if param["name"] == "limit":
                limit = param["value"].get("longValue", 100)
            elif param["name"] == "offset":
                offset = param["value"].get("longValue", 0)

        # Apply pagination
        paginated = filtered[offset : offset + limit]

        # Convert to RDS Data API format
        columns = [
            "id",
            "sku",
            "name",
            "description",
            "image_url",
            "category",
            "price",
            "current_stock",
            "low_stock_threshold",
            "active",
            "is_low_stock",
            "created_at",
            "updated_at",
        ]

        records = []
        for product in paginated:
            row = [
                self._value_to_rds(product["id"], "LONG"),
                self._value_to_rds(product["sku"], "STRING"),
                self._value_to_rds(product["name"], "STRING"),
                self._value_to_rds(product["description"], "STRING"),
                self._value_to_rds(product["image_url"], "STRING"),
                self._value_to_rds(product["category"], "STRING"),
                self._value_to_rds(product["price"], "DOUBLE"),
                self._value_to_rds(product["qty"], "LONG"),
                self._value_to_rds(product["low_stock_threshold"], "LONG"),
                self._value_to_rds(product["active"], "LONG"),
                self._value_to_rds(
                    1 if product["qty"] <= product["low_stock_threshold"] else 0, "LONG"
                ),
                self._value_to_rds(product["created_at"], "STRING"),
                self._value_to_rds(product["updated_at"], "STRING"),
            ]
            records.append(row)

        column_metadata = [{"name": col, "type": "VARCHAR"} for col in columns]

        return {
            "records": records,
            "columnMetadata": column_metadata,
        }

    def _execute_select_users(self, sql: str, parameters: list) -> dict:
        """Execute SELECT query on users."""
        records = []
        for user in self.users:
            row = [
                self._value_to_rds(user["id"], "LONG"),
                self._value_to_rds(user["username"], "STRING"),
                self._value_to_rds(user["email"], "STRING"),
                self._value_to_rds(user["created_at"], "STRING"),
            ]
            records.append(row)

        columns = ["id", "username", "email", "created_at"]
        column_metadata = [{"name": col, "type": "VARCHAR"} for col in columns]

        return {
            "records": records,
            "columnMetadata": column_metadata,
        }

    def _filter_products(self, parameters: list) -> list[dict]:
        """Apply WHERE filters to products."""
        products = self.products.copy()

        for param in parameters:
            if param["name"] == "active":
                active = param["value"].get("longValue", 1)
                products = [p for p in products if p["active"] == active]
            elif param["name"] == "category":
                category = param["value"].get("stringValue", "")
                products = [p for p in products if p["category"] == category]

        return products

    @staticmethod
    def _value_to_rds(value: Any, type_hint: str = "STRING") -> dict:
        """Convert a Python value to RDS Data API format."""
        if value is None:
            return {"isNull": True}

        if type_hint == "LONG":
            return {"longValue": int(value)}
        elif type_hint == "DOUBLE":
            return {"doubleValue": float(value)}
        else:
            return {"stringValue": str(value)}


def get_rds_client():
    """
    Get RDS client (mock or real).
    Uses mock if LOCAL_MOCK_DB env var is set.
    """
    if os.environ.get("LOCAL_MOCK_DB", "").lower() in ("1", "true", "yes"):
        print("[DEBUG] Using mock RDS client")
        return MockRDSClient()
    else:
        import boto3
        return boto3.client("rds-data")
