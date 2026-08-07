"""
Shared pytest fixtures for the stripe_webhook Lambda test suite.

Uses moto to mock DynamoDB — no real AWS resources or network calls.
Signature verification is exercised for real (see test_lambda_handler.py)
by computing a valid HMAC signature with a known test secret and
monkeypatching get_secret() to return that same secret, rather than
calling real Secrets Manager.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("ORDERS_TABLE_NAME", "orders-test")
os.environ.setdefault("PAYMENTS_TABLE_NAME", "payments-test")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET_NAME", "chonky/test/stripe_webhook_secret")
os.environ.setdefault("EVENT_BUS_NAME", "test-bus")
os.environ.setdefault("AWS_REGION", "us-east-1")
# botocore's own region auto-resolution (used by lambda_handler.py's
# module-level `boto3.client("events")`, which passes no explicit
# region_name) checks AWS_DEFAULT_REGION, not AWS_REGION — db.py's clients
# all pass region_name=os.environ["AWS_REGION"] explicitly so they don't
# need this, but anything constructed without an explicit region does.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

ORDERS_TABLE_NAME = "orders-test"
PAYMENTS_TABLE_NAME = "payments-test"

TEST_WEBHOOK_SECRET = "whsec_test_secret"


@pytest.fixture
def dynamodb_tables():
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
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        yield {
            "orders": resource.Table(ORDERS_TABLE_NAME),
            "payments": resource.Table(PAYMENTS_TABLE_NAME),
        }


@pytest.fixture
def orders_table(dynamodb_tables):
    return dynamodb_tables["orders"]


@pytest.fixture
def payments_table(dynamodb_tables):
    return dynamodb_tables["payments"]


@pytest.fixture
def db(dynamodb_tables):
    import db as db_module
    return db_module.get_db_client()
