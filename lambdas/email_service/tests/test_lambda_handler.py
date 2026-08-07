import json
from unittest.mock import MagicMock, patch

import lambda_handler as lh


def _mock_provider():
    """Patches DefaultEmailProviderFactory so no real SES call is made,
    returning the mock provider so tests can assert on/control send_* calls."""
    patcher = patch("lambda_handler.DefaultEmailProviderFactory")
    MockFactory = patcher.start()
    provider = MockFactory.return_value.get_provider.return_value
    return provider, patcher


class TestGetRecipientEmail:
    def test_prod_uses_original_address_unchanged(self, monkeypatch):
        monkeypatch.setattr(lh, "IS_DEV", False)
        monkeypatch.setattr(lh, "DEV_EMAIL", None)
        email, prefix = lh.get_recipient_email("shopper@example.com", "order abc")
        assert email == "shopper@example.com"
        assert prefix == ""

    def test_dev_mode_redirects_to_dev_email(self, monkeypatch):
        monkeypatch.setattr(lh, "IS_DEV", True)
        monkeypatch.setattr(lh, "DEV_EMAIL", "dev@chonkycat.test")
        email, prefix = lh.get_recipient_email("shopper@example.com", "order abc")
        assert email == "dev@chonkycat.test"
        assert "order abc" in prefix

    def test_dev_mode_without_dev_email_set_falls_back_to_original(self, monkeypatch):
        monkeypatch.setattr(lh, "IS_DEV", True)
        monkeypatch.setattr(lh, "DEV_EMAIL", None)
        email, prefix = lh.get_recipient_email("shopper@example.com")
        assert email == "shopper@example.com"
        assert prefix == ""


class TestLambdaHandlerRouting:
    def test_routes_payment_succeeded(self):
        with patch.object(lh, "handle_payment_succeeded", return_value={"statusCode": 200}) as mock_handle:
            event = {"source": "chonkychonk.payments", "detail-type": "PaymentSucceeded", "detail": {"order_id": "o1"}}
            result = lh.lambda_handler(event, None)
            mock_handle.assert_called_once_with({"order_id": "o1"})
            assert result == {"statusCode": 200}

    def test_routes_payment_failed(self):
        with patch.object(lh, "handle_payment_failed", return_value={"statusCode": 200}) as mock_handle:
            event = {"source": "chonkychonk.payments", "detail-type": "PaymentFailed", "detail": {"order_id": "o1"}}
            lh.lambda_handler(event, None)
            mock_handle.assert_called_once()

    def test_routes_user_created(self):
        with patch.object(lh, "handle_user_created", return_value={"statusCode": 200}) as mock_handle:
            event = {"source": "chonkychonk.users", "detail-type": "UserCreated", "detail": {"user_id": "u1"}}
            lh.lambda_handler(event, None)
            mock_handle.assert_called_once()

    def test_low_stock_acknowledged_without_sending_email(self):
        event = {"source": "chonkychonk.orders", "detail-type": "LowStockDetected", "detail": {"products": ["p1", "p2"]}}
        result = lh.lambda_handler(event, None)
        assert result["statusCode"] == 200

    def test_order_created_no_longer_sends_anything(self):
        """Regression test: confirmation emails must fire off payment
        success, not order creation — OrderCreated has no handler at all."""
        event = {"source": "chonkychonk.orders", "detail-type": "OrderCreated", "detail": {"order_id": "o1"}}
        result = lh.lambda_handler(event, None)
        assert result["statusCode"] == 400

    def test_unknown_event_returns_400(self):
        event = {"source": "chonkychonk.nonsense", "detail-type": "Whatever", "detail": {}}
        result = lh.lambda_handler(event, None)
        assert result["statusCode"] == 400

    def test_exception_in_handler_returns_500(self):
        with patch.object(lh, "handle_payment_succeeded", side_effect=RuntimeError("boom")):
            event = {"source": "chonkychonk.payments", "detail-type": "PaymentSucceeded", "detail": {}}
            result = lh.lambda_handler(event, None)
            assert result["statusCode"] == 500


class TestHandlePaymentSucceeded:
    def test_sends_confirmation_email(self):
        provider, patcher = _mock_provider()
        try:
            provider.send_order_confirmation.return_value = True
            detail = {
                "order_id": "o1", "customer_email": "shopper@example.com",
                "subtotal": "19.98", "discount": "0", "tax": "2.60", "shipping_fee": "10.00",
                "amount": "32.58", "currency": "CAD", "items": [],
                "shipping_name": "Test Guest", "shipping_address": "1 Test St",
                "promotion_code": None, "customer_notes": "Leave at the door",
            }
            result = lh.handle_payment_succeeded(detail)
            assert result["statusCode"] == 200
            provider.send_order_confirmation.assert_called_once()
            email_arg = provider.send_order_confirmation.call_args[0][0]
            assert email_arg.customer_notes == "Leave at the door"
        finally:
            patcher.stop()

    def test_dev_mode_subject_prefix_passed_to_provider(self, monkeypatch):
        monkeypatch.setattr(lh, "IS_DEV", True)
        monkeypatch.setattr(lh, "DEV_EMAIL", "dev@chonkycat.test")
        provider, patcher = _mock_provider()
        try:
            provider.send_order_confirmation.return_value = True
            detail = {
                "order_id": "o1", "customer_email": "shopper@example.com",
                "subtotal": "19.98", "discount": "0", "tax": "2.60", "shipping_fee": "10.00",
                "amount": "32.58", "currency": "CAD", "items": [],
                "shipping_name": "Test Guest", "shipping_address": "1 Test St",
            }
            lh.handle_payment_succeeded(detail)
            prefix = provider.send_order_confirmation.call_args.kwargs["subject_prefix"]
            assert "order o1" in prefix
        finally:
            patcher.stop()

    def test_missing_customer_email_returns_400(self):
        result = lh.handle_payment_succeeded({"order_id": "o1"})
        assert result["statusCode"] == 400

    def test_missing_order_id_returns_400(self):
        result = lh.handle_payment_succeeded({"customer_email": "a@b.com"})
        assert result["statusCode"] == 400

    def test_ses_failure_returns_500(self):
        provider, patcher = _mock_provider()
        try:
            provider.send_order_confirmation.return_value = False
            detail = {
                "order_id": "o1", "customer_email": "shopper@example.com",
                "subtotal": "19.98", "discount": "0", "tax": "2.60", "shipping_fee": "10.00",
                "amount": "32.58", "currency": "CAD", "items": [],
                "shipping_name": "Test Guest", "shipping_address": "1 Test St",
            }
            result = lh.handle_payment_succeeded(detail)
            assert result["statusCode"] == 500
        finally:
            patcher.stop()

    def test_unexpected_error_returns_500(self):
        with patch("lambda_handler.DefaultEmailProviderFactory", side_effect=RuntimeError("boom")):
            detail = {
                "order_id": "o1", "customer_email": "shopper@example.com",
                "subtotal": "19.98", "discount": "0", "tax": "2.60", "shipping_fee": "10.00",
                "amount": "32.58", "currency": "CAD", "items": [],
                "shipping_name": "Test Guest", "shipping_address": "1 Test St",
            }
            result = lh.handle_payment_succeeded(detail)
            assert result["statusCode"] == 500


class TestHandlePaymentFailed:
    def test_sends_failure_email(self):
        provider, patcher = _mock_provider()
        try:
            provider.send_order_failure.return_value = True
            detail = {"order_id": "o1", "customer_email": "shopper@example.com", "reason": "Card declined"}
            result = lh.handle_payment_failed(detail)
            assert result["statusCode"] == 200
            email_arg = provider.send_order_failure.call_args[0][0]
            assert email_arg.error_message == "Card declined"
        finally:
            patcher.stop()

    def test_defaults_reason_when_missing(self):
        provider, patcher = _mock_provider()
        try:
            provider.send_order_failure.return_value = True
            detail = {"order_id": "o1", "customer_email": "shopper@example.com"}
            lh.handle_payment_failed(detail)
            email_arg = provider.send_order_failure.call_args[0][0]
            assert email_arg.error_message == "Unknown error"
        finally:
            patcher.stop()

    def test_missing_required_fields_returns_400(self):
        result = lh.handle_payment_failed({"order_id": "o1"})
        assert result["statusCode"] == 400

    def test_ses_failure_returns_500(self):
        provider, patcher = _mock_provider()
        try:
            provider.send_order_failure.return_value = False
            detail = {"order_id": "o1", "customer_email": "shopper@example.com"}
            result = lh.handle_payment_failed(detail)
            assert result["statusCode"] == 500
        finally:
            patcher.stop()

    def test_unexpected_error_returns_500(self):
        with patch("lambda_handler.DefaultEmailProviderFactory", side_effect=RuntimeError("boom")):
            detail = {"order_id": "o1", "customer_email": "shopper@example.com"}
            result = lh.handle_payment_failed(detail)
            assert result["statusCode"] == 500


class TestHandleUserCreated:
    def test_sends_welcome_email(self):
        provider, patcher = _mock_provider()
        try:
            provider.send_welcome_email.return_value = True
            detail = {"user_id": "u1", "email": "new@example.com", "first_name": "Newbie", "role": "customer"}
            result = lh.handle_user_created(detail)
            assert result["statusCode"] == 200
            provider.send_welcome_email.assert_called_once()
        finally:
            patcher.stop()

    def test_missing_required_fields_returns_400(self):
        result = lh.handle_user_created({"user_id": "u1"})
        assert result["statusCode"] == 400

    def test_ses_failure_returns_500(self):
        provider, patcher = _mock_provider()
        try:
            provider.send_welcome_email.return_value = False
            detail = {"user_id": "u1", "email": "new@example.com"}
            result = lh.handle_user_created(detail)
            assert result["statusCode"] == 500
        finally:
            patcher.stop()

    def test_unexpected_error_returns_500(self):
        with patch("lambda_handler.DefaultEmailProviderFactory", side_effect=RuntimeError("boom")):
            detail = {"user_id": "u1", "email": "new@example.com"}
            result = lh.handle_user_created(detail)
            assert result["statusCode"] == 500
