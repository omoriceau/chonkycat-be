"""
Shared pytest fixtures for the users Lambda test suite.

Scoped to the self-service authorization logic added for GET/PUT
/users/{userId} (see _require_self / _SELF_SERVICE_FORBIDDEN_FIELDS in
lambda_handler.py) — the service layer itself talks to real Cognito +
DynamoDB and isn't exercised here; these tests monkeypatch _get_service()
with a stub instead.
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("AWS_REGION", "us-east-1")
# botocore's own region auto-resolution (used by service.py's
# `boto3.client("events")`/`boto3.client("cognito-idp")`, which pass no
# explicit region_name) checks AWS_DEFAULT_REGION, not AWS_REGION — db.py's
# client passes region_name=os.environ["AWS_REGION"] explicitly so it
# doesn't need this, but anything constructed without an explicit region does.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("USERS_TABLE_NAME", "users-test")
os.environ.setdefault("COGNITO_USER_POOL_ID", "us-east-1_testpool")

USERS_TABLE_NAME = os.environ["USERS_TABLE_NAME"]


@pytest.fixture
def users_table():
    """Mocked users table matching the real Terraform schema
    (hash_key user_id, GSI EmailIndex hash=email)."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=USERS_TABLE_NAME,
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "email", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "EmailIndex",
                "KeySchema": [{"AttributeName": "email", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
            }],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        yield resource.Table(USERS_TABLE_NAME)


@pytest.fixture
def db(users_table):
    """A real DynamoDBClient wired up against the mocked table."""
    import db as db_module
    return db_module.get_db_client()


class _FakeUsernameExistsException(Exception):
    pass


@pytest.fixture
def cognito_mock():
    """A MagicMock standing in for the boto3 cognito-idp client. A real
    exception *class* is wired up under .exceptions so `except
    self._cognito.exceptions.UsernameExistsException` in service.py works
    as with a real client (botocore generates real exception classes per
    client too) — everything else service.py catches via the generic
    ClientError, which doesn't need this."""
    from unittest.mock import MagicMock

    mock = MagicMock()
    mock.exceptions.UsernameExistsException = _FakeUsernameExistsException
    mock.admin_create_user.return_value = {
        "User": {"Attributes": [{"Name": "sub", "Value": "cognito-sub-1"}]}
    }
    return mock


@pytest.fixture
def events_mock():
    from unittest.mock import MagicMock
    return MagicMock()


@pytest.fixture
def user_service(db, cognito_mock, events_mock):
    from service import UserService
    return UserService(db_client=db, events_client=events_mock, cognito_client=cognito_mock)


class FakeUserService:
    """Stands in for UserService — records calls, returns canned data."""

    def __init__(self):
        self.get_user_calls = []
        self.update_user_calls = []
        self.create_user_calls = []
        self.delete_user_calls = []
        self.list_users_calls = []
        self.get_user_return = {"id": "u1", "email": "shopper@example.com", "first_name": "Old"}
        self.update_user_return = {"id": "u1", "email": "shopper@example.com", "first_name": "New"}
        self.create_user_return = {"id": "u1", "email": "new@example.com", "first_name": "New"}
        self.delete_user_return = True
        self.list_users_return = {"users": [], "limit": 50, "offset": 0, "count": 0}
        # Set to an exception instance/class to make the corresponding call raise.
        self.get_user_raises = None
        self.update_user_raises = None
        self.create_user_raises = None
        self.delete_user_raises = None
        self.list_users_raises = None

    def get_user(self, user_id):
        self.get_user_calls.append(user_id)
        if self.get_user_raises:
            raise self.get_user_raises
        return self.get_user_return

    def update_user(self, user_id, update):
        self.update_user_calls.append((user_id, update))
        if self.update_user_raises:
            raise self.update_user_raises
        return self.update_user_return

    def create_user(self, request):
        self.create_user_calls.append(request)
        if self.create_user_raises:
            raise self.create_user_raises
        return self.create_user_return

    def delete_user(self, user_id):
        self.delete_user_calls.append(user_id)
        if self.delete_user_raises:
            raise self.delete_user_raises
        return self.delete_user_return

    def list_users(self, limit=50, offset=0, role=None, status=None):
        self.list_users_calls.append((limit, offset, role, status))
        if self.list_users_raises:
            raise self.list_users_raises
        return self.list_users_return


@pytest.fixture
def fake_service(monkeypatch):
    import lambda_handler
    service = FakeUserService()
    monkeypatch.setattr(lambda_handler, "_get_service", lambda: service)
    return service


@pytest.fixture
def make_event():
    def _make(method, user_id=None, body=None, authenticated_sub=None, resource=None,
              query_params=None, raw_body=None):
        event = {
            "httpMethod": method,
            "pathParameters": {"userId": user_id} if user_id else None,
        }
        if resource is not None:
            event["resource"] = resource
            event["path"] = resource.replace("{userId}", user_id or "")
        if body is not None:
            event["body"] = json.dumps(body)
        if raw_body is not None:
            event["body"] = raw_body
        if authenticated_sub is not None:
            event["requestContext"] = {"authorizer": {"claims": {"sub": authenticated_sub}}}
        if query_params is not None:
            event["queryStringParameters"] = query_params
        return event

    return _make


def body_of(resp: dict) -> dict:
    return json.loads(resp["body"]) if resp.get("body") else {}
