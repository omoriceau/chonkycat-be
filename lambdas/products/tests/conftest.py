"""
Shared pytest fixtures for the products Lambda test suite.

Uses moto to mock DynamoDB — no real AWS resources or network calls.
Every test gets a fresh, empty table (function-scoped fixture) so tests
can't leak state into one another.
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import boto3
import pytest
from moto import mock_aws

# Must be set before db.py's get_db_client() reads them.
os.environ.setdefault("PRODUCTS_TABLE_NAME", "products-test")
os.environ.setdefault("PRODUCT_IMAGES_BUCKET", "chonky-images-test")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

TABLE_NAME = "products-test"
IMAGES_BUCKET = os.environ["PRODUCT_IMAGES_BUCKET"]


@pytest.fixture
def dynamodb_table():
    """Spin up a mocked DynamoDB table matching the real Terraform schema
    (hash key + CategoryIndex + ReorderIndex GSIs) and tear it down after
    each test."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=TABLE_NAME,
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "product_id", "AttributeType": "S"},
                {"AttributeName": "category", "AttributeType": "S"},
                {"AttributeName": "name", "AttributeType": "S"},
                {"AttributeName": "reorder_flag", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "CategoryIndex",
                    "KeySchema": [
                        {"AttributeName": "category", "KeyType": "HASH"},
                        {"AttributeName": "name", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
                {
                    "IndexName": "ReorderIndex",
                    "KeySchema": [
                        {"AttributeName": "reorder_flag", "KeyType": "HASH"},
                        {"AttributeName": "product_id", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
            ],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        yield boto3.resource("dynamodb", region_name="us-east-1").Table(TABLE_NAME)


@pytest.fixture
def db(dynamodb_table):
    """A real DynamoDBClient wired up against the mocked table."""
    import db as db_module
    return db_module.get_db_client()


@pytest.fixture
def images_bucket(dynamodb_table):
    """Creates the mocked S3 bucket handlers/image.py uploads into. Depends
    on dynamodb_table (rather than starting its own mock_aws()) so both
    live inside the same moto mock context — moto mocks per-context, not
    per-service, so a second `with mock_aws():` here would just shadow the
    first and leave the DynamoDB table looking gone to any code running
    inside it."""
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=IMAGES_BUCKET)
    return IMAGES_BUCKET


@pytest.fixture
def make_event():
    """Factory for building API Gateway (REST API / v1 shape) events."""

    def _make(method, product_id=None, body=None, qs=None, resource=None):
        event = {
            "httpMethod": method,
            "pathParameters": {"productid": product_id} if product_id else None,
            "queryStringParameters": qs,
        }
        if resource is not None:
            event["resource"] = resource
            event["path"] = resource.replace("{productid}", product_id or "")
        if body is not None:
            event["body"] = json.dumps(body)
        return event

    return _make


@pytest.fixture
def make_event_v2():
    """Factory for building API Gateway HTTP API (v2, payload format 2.0) events,
    which nest the method under requestContext.http.method instead of a
    top-level httpMethod key."""

    def _make(method, product_id=None, body=None, qs=None):
        event = {
            "requestContext": {"http": {"method": method}},
            "pathParameters": {"productid": product_id} if product_id else None,
            "queryStringParameters": qs,
        }
        if body is not None:
            event["body"] = json.dumps(body)
        return event

    return _make


def body_of(resp: dict) -> dict:
    """Parse a Lambda proxy response's JSON body."""
    return json.loads(resp["body"]) if resp.get("body") else {}
