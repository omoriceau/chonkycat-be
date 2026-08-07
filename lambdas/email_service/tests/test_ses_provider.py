from unittest.mock import MagicMock

from email_service.base import EmailAddress, OrderConfirmationEmail, OrderFailureEmail, WelcomeEmail
from email_service.ses_provider import SESEmailProvider, _short_order_id


def _confirmation_email(**overrides) -> OrderConfirmationEmail:
    defaults = dict(
        to=EmailAddress(address="shopper@example.com", name="Test Guest"),
        order_id="661d1443-3593-4c14-af08-f608722b01f7",
        total_amount="32.58",
        currency="CAD",
        items=[{"name": "Test Kibble", "quantity": 2, "unit_price": "9.99", "line_total": "19.98"}],
        shipping_name="Test Guest",
        shipping_address="1 Test St, Toronto, ON, M5V 2H1, Canada",
        promotion_code=None,
        discount="0",
        subtotal="19.98",
        tax="2.60",
        shipping_fee="10.00",
    )
    defaults.update(overrides)
    return OrderConfirmationEmail(**defaults)


class TestShortOrderId:
    def test_truncates_to_eight_characters(self):
        assert _short_order_id("661d1443-3593-4c14-af08-f608722b01f7") == "661d1443"

    def test_shorter_id_returned_as_is(self):
        assert _short_order_id("abc123") == "abc123"

    def test_coerces_non_string_input(self):
        assert _short_order_id(12345678901234) == "12345678"


class TestSendOrderConfirmation:
    def test_uses_short_order_id_in_subject(self):
        provider = SESEmailProvider(ses_client=MagicMock())
        provider.send_order_confirmation(_confirmation_email())
        kwargs = provider._ses.send_email.call_args.kwargs
        assert "661d1443" in kwargs["Message"]["Subject"]["Data"]
        assert "661d1443-3593-4c14-af08-f608722b01f7" not in kwargs["Message"]["Subject"]["Data"]

    def test_subject_prefix_applied_in_dev_mode(self):
        """Regression test: this used to try email.subject = ..., but
        OrderConfirmationEmail has no such field (frozen dataclass), so the
        prefix silently never applied — dead code, now a real parameter."""
        provider = SESEmailProvider(ses_client=MagicMock())
        provider.send_order_confirmation(_confirmation_email(), subject_prefix="[DEV: order abc] ")
        kwargs = provider._ses.send_email.call_args.kwargs
        assert kwargs["Message"]["Subject"]["Data"].startswith("[DEV: order abc] ")

    def test_sends_to_formatted_address(self):
        provider = SESEmailProvider(ses_client=MagicMock())
        provider.send_order_confirmation(_confirmation_email())
        kwargs = provider._ses.send_email.call_args.kwargs
        assert kwargs["Destination"]["ToAddresses"] == ["Test Guest <shopper@example.com>"]

    def test_returns_true_on_success(self):
        provider = SESEmailProvider(ses_client=MagicMock())
        assert provider.send_order_confirmation(_confirmation_email()) is True

    def test_returns_false_on_ses_client_error(self):
        from botocore.exceptions import ClientError
        ses = MagicMock()
        ses.send_email.side_effect = ClientError({"Error": {"Code": "MessageRejected", "Message": "nope"}}, "SendEmail")
        provider = SESEmailProvider(ses_client=ses)
        assert provider.send_order_confirmation(_confirmation_email()) is False

    def test_delivery_instructions_included_when_present(self):
        provider = SESEmailProvider(ses_client=MagicMock())
        email = _confirmation_email(customer_notes="Leave at the side door")
        html = provider._render_confirmation_html(email)
        text = provider._render_confirmation_text(email)
        assert "Leave at the side door" in html
        assert "Leave at the side door" in text

    def test_delivery_instructions_omitted_when_absent(self):
        provider = SESEmailProvider(ses_client=MagicMock())
        html = provider._render_confirmation_html(_confirmation_email(customer_notes=None))
        assert "Delivery instructions" not in html

    def test_delivery_instructions_html_escaped(self):
        """customer_notes is free-text the shopper typed at checkout —
        must not be interpretable as HTML/script in the rendered email."""
        provider = SESEmailProvider(ses_client=MagicMock())
        email = _confirmation_email(customer_notes="<script>alert(1)</script>")
        html = provider._render_confirmation_html(email)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_promotion_shown_when_applied(self):
        provider = SESEmailProvider(ses_client=MagicMock())
        email = _confirmation_email(promotion_code="WELCOME10", discount="2.00")
        html = provider._render_confirmation_html(email)
        text = provider._render_confirmation_text(email)
        assert "WELCOME10" in html
        assert "WELCOME10" in text

    def test_items_and_total_rendered(self):
        provider = SESEmailProvider(ses_client=MagicMock())
        html = provider._render_confirmation_html(_confirmation_email())
        assert "Test Kibble" in html
        assert "32.58" in html


class TestSendOrderFailure:
    def _failure_email(self, **overrides) -> OrderFailureEmail:
        defaults = dict(
            to=EmailAddress(address="shopper@example.com"),
            order_id="661d1443-3593-4c14-af08-f608722b01f7",
            error_message="Your card was declined.",
            support_email="support@chonkycat.test",
        )
        defaults.update(overrides)
        return OrderFailureEmail(**defaults)

    def test_uses_short_order_id_in_subject_and_body(self):
        provider = SESEmailProvider(ses_client=MagicMock())
        email = self._failure_email()
        provider.send_order_failure(email)
        kwargs = provider._ses.send_email.call_args.kwargs
        assert "661d1443" in kwargs["Message"]["Subject"]["Data"]
        assert "661d1443-3593-4c14-af08-f608722b01f7" not in kwargs["Message"]["Subject"]["Data"]

    def test_reason_included_in_body(self):
        provider = SESEmailProvider(ses_client=MagicMock())
        html = provider._render_failure_html(self._failure_email(error_message="Insufficient funds"))
        text = provider._render_failure_text(self._failure_email(error_message="Insufficient funds"))
        assert "Insufficient funds" in html
        assert "Insufficient funds" in text

    def test_subject_prefix_applied_in_dev_mode(self):
        provider = SESEmailProvider(ses_client=MagicMock())
        provider.send_order_failure(self._failure_email(), subject_prefix="[DEV: order abc] ")
        kwargs = provider._ses.send_email.call_args.kwargs
        assert kwargs["Message"]["Subject"]["Data"].startswith("[DEV: order abc] ")


class TestSendWelcomeEmail:
    def test_sends_and_returns_true(self):
        provider = SESEmailProvider(ses_client=MagicMock())
        email = WelcomeEmail(to=EmailAddress(address="new@example.com", name="Newbie"), first_name="Newbie")
        assert provider.send_welcome_email(email) is True

    def test_subject_prefix_applied(self):
        provider = SESEmailProvider(ses_client=MagicMock())
        email = WelcomeEmail(to=EmailAddress(address="new@example.com"), first_name=None)
        provider.send_welcome_email(email, subject_prefix="[DEV] ")
        kwargs = provider._ses.send_email.call_args.kwargs
        assert kwargs["Message"]["Subject"]["Data"].startswith("[DEV] ")
