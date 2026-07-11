"""
Shared pytest fixtures for the orders Lambda test suite.

Uses moto to mock DynamoDB — no real AWS resources or network calls. Every
test gets fresh, empty tables (function-scoped fixture) so tests can't leak
state into one another.
"""

import json
import os
import sys
from unittest.mock import MagicMock

_HERE = os.path.dirname(__file__)
# lambdas/orders — so `import db`, `import service`, `import lambda_handler`
# resolve the same way they do inside the Lambda's own CodeUri.
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))
# Repo root — the SharedLayer's contents also live at shared/ here (mirrored
# into shared/python/shared/ for the actual Lambda layer bundle), so this
# makes `from shared.cors import ...` / `from shared.events import ...`
# resolve locally the same way they do at runtime.
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..", "..")))

import boto3
import pytest
from moto import mock_aws

# Must be set before db.py's get_db_client() reads them.
os.environ.setdefault("ORDERS_TABLE_NAME", "orders-test")
os.environ.setdefault("PRODUCTS_TABLE_NAME", "products-test")
os.environ.setdefault("PROMOTIONS_TABLE_NAME", "promotions-test")
os.environ.setdefault("EVENT_BUS_NAME", "test-bus")
os.environ.setdefault("CUSTOMER_COGNITO_USER_POOL_ID", "")
os.environ.setdefault("CUSTOMER_COGNITO_APP_CLIENT_ID", "")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

ORDERS_TABLE_NAME = "orders-test"
PRODUCTS_TABLE_NAME = "products-test"


@pytest.fixture
def dynamodb_tables():
    """Spin up mocked orders + products tables matching the real Terraform
    schema (orders: hash order_id/range sk, sparse UserOrdersIndex +
    StatusIndex GSIs) and tear them down after each test."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=ORDERS_TABLE_NAME,
            KeySchema=[
                {"AttributeName": "order_id", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "order_id", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "UserOrdersIndex",
                    "KeySchema": [
                        {"AttributeName": "user_id", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
                {
                    "IndexName": "StatusIndex",
                    "KeySchema": [
                        {"AttributeName": "status", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
            ],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        client.create_table(
            TableName=PRODUCTS_TABLE_NAME,
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        yield {
            "orders": resource.Table(ORDERS_TABLE_NAME),
            "products": resource.Table(PRODUCTS_TABLE_NAME),
        }


@pytest.fixture
def orders_table(dynamodb_tables):
    return dynamodb_tables["orders"]


@pytest.fixture
def products_table(dynamodb_tables):
    return dynamodb_tables["products"]


@pytest.fixture
def db(dynamodb_tables):
    """A real DynamoDBClient wired up against the mocked tables."""
    import db as db_module
    return db_module.get_db_client()


@pytest.fixture
def events_client():
    """Stand-in for the boto3 EventBridge client — just records put_events
    calls instead of hitting real EventBridge."""
    return MagicMock()


@pytest.fixture
def service(db, events_client):
    import service as service_module
    return service_module.OrderService(db_client=db, events_client=events_client)


@pytest.fixture
def make_product():
    """Factory for seeding a product row directly via the mocked table."""

    def _make(products_table, product_id="prod-1", name="Chonky Salmon", price="24.99",
              qty=40, active=True, low_stock_threshold=5):
        products_table.put_item(Item={
            "product_id": product_id,
            "name": name,
            "price": price,
            "qty": qty,
            "active": active,
            "low_stock_threshold": low_stock_threshold,
        })
        return product_id

    return _make


@pytest.fixture
def make_event():
    """Factory for building API Gateway (REST API / v1 shape) events,
    including `resource` (used by lambda_handler.py to route /cart*)."""

    def _make(method, resource=None, path_params=None, body=None, qs=None, headers=None):
        event = {
            "httpMethod": method,
            "resource": resource,
            "pathParameters": path_params,
            "queryStringParameters": qs,
            "headers": headers or {},
        }
        if body is not None:
            event["body"] = json.dumps(body)
        return event

    return _make


def body_of(resp: dict) -> dict:
    """Parse a Lambda proxy response's JSON body."""
    return json.loads(resp["body"]) if resp.get("body") else {}
