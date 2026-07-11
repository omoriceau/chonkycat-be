import json
from unittest.mock import MagicMock

from tests.conftest import body_of


class _FakePayload:
    def __init__(self, data):
        self._data = json.dumps(data).encode()

    def read(self):
        return self._data


def _stub_stripe_success(monkeypatch, intent_id="pi_123", client_secret="pi_123_secret"):
    import lambda_handler
    fake_lambda = MagicMock()
    fake_lambda.invoke.return_value = {
        "StatusCode": 200,
        "Payload": _FakePayload({"intent_id": intent_id, "client_secret": client_secret}),
    }
    monkeypatch.setattr(lambda_handler, "_lambda", fake_lambda)
    monkeypatch.setattr(lambda_handler, "_events", MagicMock())
    return fake_lambda


class TestCreatePaymentIntent:
    def test_happy_path(self, dynamodb_tables, orders_table, users_table, payments_table, make_event, monkeypatch):
        import lambda_handler
        _stub_stripe_success(monkeypatch)

        orders_table.put_item(Item={"order_id": "o1", "sk": "ORDER", "status": "pending", "total_amount": "24.99", "user_id": "u1"})
        users_table.put_item(Item={"user_id": "u1", "email": "benny@example.com"})

        resp = lambda_handler.lambda_handler(make_event(body={"order_id": "o1"}), None)

        assert resp["statusCode"] == 200
        data = body_of(resp)
        assert data["order_id"] == "o1"
        assert data["client_secret"] == "pi_123_secret"

        # payments_api's whole reason for writing to DynamoDB at all — the
        # record stripe_webhook will look up later via ProviderTxnIndex.
        payment = payments_table.get_item(Key={"order_id": "o1", "sk": "PAYMENT#pi_123"})["Item"]
        assert payment["status"] == "pending"
        assert payment["amount"] == "24.99"

    def test_missing_order_id(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler
        _stub_stripe_success(monkeypatch)

        resp = lambda_handler.lambda_handler(make_event(body={}), None)
        assert resp["statusCode"] == 422

    def test_order_not_found(self, dynamodb_tables, make_event, monkeypatch):
        import lambda_handler
        _stub_stripe_success(monkeypatch)

        resp = lambda_handler.lambda_handler(make_event(body={"order_id": "nope"}), None)
        assert resp["statusCode"] == 422

    def test_order_not_pending_rejected(self, dynamodb_tables, orders_table, users_table, make_event, monkeypatch):
        import lambda_handler
        _stub_stripe_success(monkeypatch)

        orders_table.put_item(Item={"order_id": "o1", "sk": "ORDER", "status": "completed", "total_amount": "24.99", "user_id": "u1"})
        users_table.put_item(Item={"user_id": "u1", "email": "benny@example.com"})

        resp = lambda_handler.lambda_handler(make_event(body={"order_id": "o1"}), None)
        assert resp["statusCode"] == 409

    def test_stripe_lambda_failure_surfaces_as_500(self, dynamodb_tables, orders_table, users_table, make_event, monkeypatch):
        import lambda_handler
        fake_lambda = MagicMock()
        fake_lambda.invoke.return_value = {
            "StatusCode": 200,
            "FunctionError": "Unhandled",
            "Payload": _FakePayload({"errorMessage": "card declined"}),
        }
        monkeypatch.setattr(lambda_handler, "_lambda", fake_lambda)
        monkeypatch.setattr(lambda_handler, "_events", MagicMock())

        orders_table.put_item(Item={"order_id": "o1", "sk": "ORDER", "status": "pending", "total_amount": "24.99", "user_id": "u1"})
        users_table.put_item(Item={"user_id": "u1", "email": "benny@example.com"})

        resp = lambda_handler.lambda_handler(make_event(body={"order_id": "o1"}), None)
        assert resp["statusCode"] == 500
