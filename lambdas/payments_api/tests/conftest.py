"""
Shared pytest fixtures for the payments_api Lambda test suite.

Uses moto to mock DynamoDB — no real AWS resources or network calls. The
Stripe/Lambda-invoke side of the flow is stubbed directly in tests that
need it (see test_lambda_handler.py) rather than mocked here, since it's
specific to each scenario.
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("ORDERS_TABLE_NAME", "orders-test")
os.environ.setdefault("PAYMENTS_TABLE_NAME", "payments-test")
os.environ.setdefault("USERS_TABLE_NAME", "users-test")
os.environ.setdefault("STRIPE_INTENT_FUNCTION_ARN", "arn:aws:lambda:us-east-1:123456789012:function:stripe-intent-test")
os.environ.setdefault("EVENT_BUS_NAME", "test-bus")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

ORDERS_TABLE_NAME = "orders-test"
PAYMENTS_TABLE_NAME = "payments-test"
USERS_TABLE_NAME = "users-test"


@pytest.fixture
def dynamodb_tables():
    """Mocked orders + payments + users tables matching the real Terraform
    schema (payments: hash order_id/range sk, ProviderTxnIndex GSI)."""
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
            ],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        client.create_table(
            TableName=PAYMENTS_TABLE_NAME,
            KeySchema=[
                {"AttributeName": "order_id", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "order_id", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "provider_transaction_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "ProviderTxnIndex",
                "KeySchema": [{"AttributeName": "provider_transaction_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
            }],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        client.create_table(
            TableName=USERS_TABLE_NAME,
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        yield {
            "orders": resource.Table(ORDERS_TABLE_NAME),
            "payments": resource.Table(PAYMENTS_TABLE_NAME),
            "users": resource.Table(USERS_TABLE_NAME),
        }


@pytest.fixture
def orders_table(dynamodb_tables):
    return dynamodb_tables["orders"]


@pytest.fixture
def payments_table(dynamodb_tables):
    return dynamodb_tables["payments"]


@pytest.fixture
def users_table(dynamodb_tables):
    return dynamodb_tables["users"]


@pytest.fixture
def db(dynamodb_tables):
    """A real DynamoDBClient wired up against the mocked tables."""
    import db as db_module
    return db_module.get_db_client()


@pytest.fixture
def make_event():
    """Factory for building API Gateway (REST API / v1 shape) events."""

    def _make(body=None):
        event = {"httpMethod": "POST"}
        if body is not None:
            event["body"] = json.dumps(body)
        return event

    return _make


def body_of(resp: dict) -> dict:
    return json.loads(resp["body"]) if resp.get("body") else {}
