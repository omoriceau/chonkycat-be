import hashlib
import hmac
import json
import time
from decimal import Decimal
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


class TestCentsToAmount:
    def test_converts_cents_to_decimal_dollars(self):
        import lambda_handler
        assert lambda_handler._cents_to_amount(9376) == Decimal("93.76")

    def test_rounds_to_two_decimal_places(self):
        import lambda_handler
        assert lambda_handler._cents_to_amount(100) == Decimal("1.00")

    def test_zero_cents_returns_zero(self):
        import lambda_handler
        assert lambda_handler._cents_to_amount(0) == Decimal("0.00")


class TestOrderEmailFields:
    def test_returns_full_detail_for_existing_order(self, dynamodb_tables, orders_table, monkeypatch):
        lambda_handler = _patch_secret_and_events(monkeypatch)
        orders_table.put_item(Item={
            "order_id": "o1", "sk": "ORDER", "status": "pending",
            "customer_email": "shopper@example.com",
            "customer_notes": "Leave at the side door",
            "subtotal": "19.98", "tax_amount": "2.60", "shipping_amount": "10.00",
            "shipping_name": "Test Guest", "shipping_address1": "1 Test St",
            "shipping_city": "Toronto", "shipping_province": "ON",
            "shipping_postal_code": "M5V 2H1", "shipping_country": "Canada",
            "applied_promotions": [{"code": "WELCOME10", "discount_amount": "2.00"}],
        })
        orders_table.put_item(Item={
            "order_id": "o1", "sk": "ITEM#0000", "name_snapshot": "Test Kibble",
            "quantity": 2, "unit_price": "9.99", "line_total": "19.98",
        })

        import db as db_module
        fields = lambda_handler._order_email_fields(db_module.get_db_client(), "o1")

        assert fields["customer_email"] == "shopper@example.com"
        assert fields["customer_notes"] == "Leave at the side door"
        assert fields["subtotal"] == "19.98"
        assert fields["tax"] == "2.60"
        assert fields["shipping_fee"] == "10.00"
        assert fields["discount"] == "2.00"
        assert fields["promotion_code"] == "WELCOME10"
        assert fields["shipping_address"] == "1 Test St, Toronto, ON, M5V 2H1, Canada"
        assert fields["items"] == [
            {"name": "Test Kibble", "quantity": 2, "unit_price": "9.99", "line_total": "19.98"}
        ]

    def test_returns_empty_dict_when_order_not_found(self, dynamodb_tables, orders_table, monkeypatch):
        lambda_handler = _patch_secret_and_events(monkeypatch)
        import db as db_module
        assert lambda_handler._order_email_fields(db_module.get_db_client(), "missing") == {}

    def test_no_discount_or_promo_when_none_applied(self, dynamodb_tables, orders_table, monkeypatch):
        lambda_handler = _patch_secret_and_events(monkeypatch)
        orders_table.put_item(Item={
            "order_id": "o1", "sk": "ORDER", "status": "pending",
            "customer_email": "shopper@example.com",
            "subtotal": "9.99", "tax_amount": "1.30", "shipping_amount": "10.00",
            "shipping_name": "Test Guest", "shipping_address1": "1 Test St",
            "shipping_city": "Toronto", "shipping_province": "ON",
            "shipping_postal_code": "M5V 2H1", "shipping_country": "Canada",
        })

        import db as db_module
        fields = lambda_handler._order_email_fields(db_module.get_db_client(), "o1")

        assert fields["discount"] == "0"
        assert fields["promotion_code"] is None
        assert fields["customer_notes"] is None


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

    def test_emitted_event_carries_full_email_detail(self, dynamodb_tables, orders_table, monkeypatch):
        """The confirmation email is built entirely from this event's detail
        (email_service never touches DynamoDB itself) — regression test for
        the order/item enrichment added alongside moving the email trigger
        from order-creation to payment-success."""
        lambda_handler = _patch_secret_and_events(monkeypatch)
        orders_table.put_item(Item={
            "order_id": "o1", "sk": "ORDER", "status": "pending",
            "customer_email": "shopper@example.com",
            "shipping_name": "Test Guest", "shipping_address1": "1 Test St",
            "shipping_city": "Toronto", "shipping_province": "ON",
            "shipping_postal_code": "M5V 2H1", "shipping_country": "Canada",
            "subtotal": "24.99", "tax_amount": "3.25", "shipping_amount": "0",
        })
        orders_table.put_item(Item={
            "order_id": "o1", "sk": "ITEM#0000", "name_snapshot": "Salmon Crisps",
            "quantity": 1, "unit_price": "24.99", "line_total": "24.99",
        })

        request = _make_request(_webhook_event("payment_intent.succeeded", "o1"))
        lambda_handler.lambda_handler(request, None)

        entry = lambda_handler._events.put_events.call_args.kwargs["Entries"][0]
        detail = json.loads(entry["Detail"])
        assert entry["DetailType"] == "PaymentSucceeded"
        assert detail["customer_email"] == "shopper@example.com"
        assert detail["amount"] == "24.99"  # 2499 cents from _webhook_event's default
        assert detail["currency"] == "CAD"
        assert detail["items"] == [
            {"name": "Salmon Crisps", "quantity": 1, "unit_price": "24.99", "line_total": "24.99"}
        ]


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

    def test_emitted_event_carries_reason_and_customer_email(self, dynamodb_tables, orders_table, monkeypatch):
        lambda_handler = _patch_secret_and_events(monkeypatch)
        orders_table.put_item(Item={
            "order_id": "o1", "sk": "ORDER", "status": "pending",
            "customer_email": "shopper@example.com",
        })

        request = _make_request(_webhook_event(
            "payment_intent.payment_failed", "o1",
            extra={"last_payment_error": {"message": "Your card was declined."}},
        ))
        lambda_handler.lambda_handler(request, None)

        entry = lambda_handler._events.put_events.call_args.kwargs["Entries"][0]
        detail = json.loads(entry["Detail"])
        assert entry["DetailType"] == "PaymentFailed"
        assert detail["reason"] == "Your card was declined."
        assert detail["customer_email"] == "shopper@example.com"


class TestUnhandledEventType:
    def test_acknowledged_without_error(self, dynamodb_tables, monkeypatch):
        lambda_handler = _patch_secret_and_events(monkeypatch)
        request = _make_request({"type": "charge.refunded", "data": {"object": {}}})
        resp = lambda_handler.lambda_handler(request, None)
        assert resp["statusCode"] == 200
