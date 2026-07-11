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

import pytest

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")


class FakeUserService:
    """Stands in for UserService — records calls, returns canned data."""

    def __init__(self):
        self.get_user_calls = []
        self.update_user_calls = []
        self.get_user_return = {"id": "u1", "email": "shopper@example.com", "first_name": "Old"}
        self.update_user_return = {"id": "u1", "email": "shopper@example.com", "first_name": "New"}

    def get_user(self, user_id):
        self.get_user_calls.append(user_id)
        return self.get_user_return

    def update_user(self, user_id, update):
        self.update_user_calls.append((user_id, update))
        return self.update_user_return


@pytest.fixture
def fake_service(monkeypatch):
    import lambda_handler
    service = FakeUserService()
    monkeypatch.setattr(lambda_handler, "_get_service", lambda: service)
    return service


@pytest.fixture
def make_event():
    def _make(method, user_id=None, body=None, authenticated_sub=None):
        event = {
            "httpMethod": method,
            "pathParameters": {"userId": user_id} if user_id else None,
        }
        if body is not None:
            event["body"] = json.dumps(body)
        if authenticated_sub is not None:
            event["requestContext"] = {"authorizer": {"claims": {"sub": authenticated_sub}}}
        return event

    return _make


def body_of(resp: dict) -> dict:
    return json.loads(resp["body"]) if resp.get("body") else {}
