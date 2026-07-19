import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock

from tests.conftest import TEST_WEBHOOK_SECRET


def _sign(body: str, secret: str = TEST_WEBHOOK_SECRET) -> str:
    timestamp = str(int(time.time()))
    signed_content = f"{timestamp}.{body}"
    signature = hmac.new(secret.encode(), signed_content.encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


def _sign_with_trailing_v0(body: str, secret: str = TEST_WEBHOOK_SECRET) -> str:
    """
    Some Stripe API versions include a legacy v0 signature alongside v1 in
    the same header, e.g. "t=...,v1=...,v0=...". v0 here is deliberately
    garbage — a correct implementation ignores it entirely.
    """
    return _sign(body, secret) + ",v0=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def _webhook_event(event_type: str, order_id: str, intent_id: str = "pi_123", extra: dict | None = None) -> dict:
    obj = {"id": intent_id, "metadata": {"order_id": order_id}, "amount": 2499, "currency": "cad"}
    if extra:
        obj.update(extra)
    return {"type": event_type, "data": {"object": obj}}


def _make_request(body_dict: dict) -> dict:
    body = json.dumps(body_dict)
    return {"body": body, "headers": {"stripe-signature": _sign(body)}}


def _patch_secret_and_events(monkeypatch):
    import lambda_handler
    monkeypatch.setattr(lambda_handler, "get_secret", lambda name: TEST_WEBHOOK_SECRET)
    monkeypatch.setattr(lambda_handler, "_events", MagicMock())
    return lambda_handler


class TestSignatureVerification:
    def test_rejects_missing_signature(self, dynamodb_tables, monkeypatch):
        lambda_handler = _patch_secret_and_events(monkeypatch)
        resp = lambda_handler.lambda_handler({"body": "{}", "headers": {}}, None)
        assert resp["statusCode"] == 401

    def test_rejects_bad_signature(self, dynamodb_tables, monkeypatch):
        lambda_handler = _patch_secret_and_events(monkeypatch)
        body = json.dumps(_webhook_event("payment_intent.succeeded", "o1"))
        resp = lambda_handler.lambda_handler(
            {"body": body, "headers": {"stripe-signature": "t=123,v1=deadbeef"}}, None
        )
        assert resp["statusCode"] == 401

    def test_accepts_capitalized_header_name(self, dynamodb_tables, orders_table, monkeypatch):
        """
        Stripe sends "Stripe-Signature" (capitalized), and API Gateway's REST
        API proxy integration preserves that casing verbatim in event
        ["headers"] rather than lowercasing it — a lowercase-only lookup
        misses every real webhook. Regression test for that bug.
        """
        lambda_handler = _patch_secret_and_events(monkeypatch)
        orders_table.put_item(Item={"order_id": "o1", "sk": "ORDER", "status": "pending"})
        body = json.dumps(_webhook_event("payment_intent.succeeded", "o1"))
        resp = lambda_handler.lambda_handler(
            {"body": body, "headers": {"Stripe-Signature": _sign(body)}}, None
        )
        assert resp["statusCode"] == 200
        assert orders_table.get_item(Key={"order_id": "o1", "sk": "ORDER"})["Item"]["status"] == "completed"


class TestPaymentIntentSucceeded:
    def test_updates_order_and_payment(self, dynamodb_tables, orders_table, payments_table, monkeypatch):
        lambda_handler = _patch_secret_and_events(monkeypatch)

        orders_table.put_item(Item={"order_id": "o1", "sk": "ORDER", "status": "pending"})
        payments_table.put_item(Item={
            "order_id": "o1", "sk": "PAYMENT#pi_123",
            "provider_transaction_id": "pi_123", "status": "pending",
        })

        request = _make_request(_webhook_event("payment_intent.succeeded", "o1"))
        resp = lambda_handler.lambda_handler(request, None)

        assert resp["statusCode"] == 200
        assert orders_table.get_item(Key={"order_id": "o1", "sk": "ORDER"})["Item"]["status"] == "completed"
        assert payments_table.get_item(Key={"order_id": "o1", "sk": "PAYMENT#pi_123"})["Item"]["status"] == "succeeded"
        lambda_handler._events.put_events.assert_called_once()

    def test_missing_payment_record_does_not_fail_the_webhook(self, dynamodb_tables, orders_table, monkeypatch):
        """The order status update is what matters — a missing payment
        record (e.g. payments_api's write failed) shouldn't 500 the
        webhook, since Stripe will keep retrying an error response."""
        lambda_handler = _patch_secret_and_events(monkeypatch)
        orders_table.put_item(Item={"order_id": "o1", "sk": "ORDER", "status": "pending"})

        request = _make_request(_webhook_event("payment_intent.succeeded", "o1"))
        resp = lambda_handler.lambda_handler(request, None)

        assert resp["statusCode"] == 200
        assert orders_table.get_item(Key={"order_id": "o1", "sk": "ORDER"})["Item"]["status"] == "completed"


class TestPaymentIntentFailed:
    def test_updates_order_and_payment(self, dynamodb_tables, orders_table, payments_table, monkeypatch):
        lambda_handler = _patch_secret_and_events(monkeypatch)

        orders_table.put_item(Item={"order_id": "o1", "sk": "ORDER", "status": "pending"})
        payments_table.put_item(Item={
            "order_id": "o1", "sk": "PAYMENT#pi_123",
            "provider_transaction_id": "pi_123", "status": "pending",
        })

        request = _make_request(_webhook_event(
            "payment_intent.payment_failed", "o1",
            extra={"last_payment_error": {"message": "Your card was declined."}},
        ))
        resp = lambda_handler.lambda_handler(request, None)

        assert resp["statusCode"] == 200
        assert orders_table.get_item(Key={"order_id": "o1", "sk": "ORDER"})["Item"]["status"] == "failed"
        payment = payments_table.get_item(Key={"order_id": "o1", "sk": "PAYMENT#pi_123"})["Item"]
        assert payment["status"] == "failed"
        assert payment["error_message"] == "Your card was declined."


class TestUnhandledEventType:
    def test_acknowledged_without_error(self, dynamodb_tables, monkeypatch):
        lambda_handler = _patch_secret_and_events(monkeypatch)
        request = _make_request({"type": "charge.refunded", "data": {"object": {}}})
        resp = lambda_handler.lambda_handler(request, None)
        assert resp["statusCode"] == 200
