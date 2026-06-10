"""
email/lambda_handler.py

Dedicated Email Lambda — handles all customer and internal emails.

Triggered by EventBridge events on the chonkychonk-bus.
Configure one EventBridge rule per detail-type pointing at this Lambda,
or a single rule with multiple detail-type values.

Supported detail-type values  (from shared/events.py):
  PaymentSettled        → order confirmation to customer
  PaymentFailed         → payment failure to customer
  RefundComplete        → refund confirmation to customer
  LowStockDetected      → stock alert to ops/admin team
  UserRegistered        → welcome email to new user
  PasswordResetRequest  → password reset link to user (stub until Cognito)
  OrderSummaryRequest   → order history email to user

Environment Variables:
  - EMAIL_PROVIDER         default: ses
  - EMAIL_FROM_ADDRESS     Verified SES sender
  - EMAIL_FROM_NAME        Display name
  - SUPPORT_EMAIL          Shown in customer-facing emails
  - LOW_STOCK_RECIPIENT    Internal email for stock alerts (e.g. ops@chonkychonk.com)
"""

import json
import logging
import os

from email.providers.base import (
    EmailAddress,
    OrderConfirmationContext,
    OrderFailureContext,
    RefundConfirmationContext,
    LowStockAlertContext,
    WelcomeEmailContext,
    PasswordResetContext,
    OrderSummaryContext,
)
from email.providers.factory import DefaultEmailProviderFactory
from shared.events import (
    PAYMENT_SETTLED,
    PAYMENT_FAILED,
    REFUND_COMPLETE,
    LOW_STOCK_DETECTED,
    USER_REGISTERED,
    PASSWORD_RESET_REQUEST,
    ORDER_SUMMARY_REQUEST,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

EMAIL_PROVIDER      = os.environ.get("EMAIL_PROVIDER",      "ses")
SUPPORT_EMAIL       = os.environ.get("SUPPORT_EMAIL",       "support@chonkychonk.com")
LOW_STOCK_RECIPIENT = os.environ.get("LOW_STOCK_RECIPIENT", "ops@chonkychonk.com")

_factory  = DefaultEmailProviderFactory()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _addr(email: str, name: str | None = None) -> EmailAddress:
    return EmailAddress(address=email, name=name or None)


def _provider():
    return _factory.get_provider(EMAIL_PROVIDER)


# ---------------------------------------------------------------------------
# Event extraction
# ---------------------------------------------------------------------------

def extract_detail(event: dict) -> tuple[str, dict]:
    """
    Returns (detail_type, detail_dict) from an EventBridge event.
    Raises ValueError if the event shape is unrecognised.
    """
    # Standard EventBridge shape
    if "detail-type" in event and "detail" in event:
        return event["detail-type"], event["detail"]

    # Direct invocation for testing: {"detail-type": "...", "detail": {...}}
    if "detail_type" in event:
        return event["detail_type"], event.get("detail", event)

    raise ValueError(f"Unrecognised event shape: {list(event.keys())}")


# ---------------------------------------------------------------------------
# Route handlers — one per event type
# ---------------------------------------------------------------------------

def handle_payment_settled(detail: dict) -> None:
    ctx = OrderConfirmationContext(
        to               = _addr(detail["customer_email"], detail.get("customer_name")),
        order_id         = detail["order_id"],
        total_amount     = detail["amount"],
        currency         = detail.get("currency", "CAD"),
        items            = detail.get("items", []),
        shipping_name    = detail.get("shipping_name", ""),
        shipping_address = detail.get("shipping_address", ""),
        promotion_code   = detail.get("promotion_code"),
        discount         = detail.get("discount", "0.00"),
        subtotal         = detail.get("subtotal", "0.00"),
        tax              = detail.get("tax", "0.00"),
        shipping_fee     = detail.get("shipping_fee", "0.00"),
    )
    sent = _provider().send_order_confirmation(ctx)
    logger.info("Order confirmation | order=%s sent=%s", detail["order_id"], sent)


def handle_payment_failed(detail: dict) -> None:
    ctx = OrderFailureContext(
        to            = _addr(detail["customer_email"], detail.get("customer_name")),
        order_id      = detail["order_id"],
        error_message = detail.get("error_message", "Payment could not be processed."),
        support_email = SUPPORT_EMAIL,
    )
    sent = _provider().send_order_failure(ctx)
    logger.info("Order failure email | order=%s sent=%s", detail["order_id"], sent)


def handle_refund_complete(detail: dict) -> None:
    ctx = RefundConfirmationContext(
        to         = _addr(detail["customer_email"], detail.get("customer_name")),
        order_id   = detail["order_id"],
        payment_id = detail["payment_id"],
        refund_id  = detail["refund_id"],
        amount     = detail["amount"],
        currency   = detail.get("currency", "CAD"),
    )
    sent = _provider().send_refund_confirmation(ctx)
    logger.info("Refund email | order=%s sent=%s", detail["order_id"], sent)


def handle_low_stock(detail: dict) -> None:
    ctx = LowStockAlertContext(
        to       = _addr(LOW_STOCK_RECIPIENT, "ChonkyChonk Ops"),
        products = detail.get("products", []),
    )
    sent = _provider().send_low_stock_alert(ctx)
    logger.info("Low stock alert | products=%s sent=%s", len(ctx.products), sent)


def handle_user_registered(detail: dict) -> None:
    ctx = WelcomeEmailContext(
        to         = _addr(detail["email"], detail.get("first_name")),
        first_name = detail.get("first_name"),
    )
    sent = _provider().send_welcome(ctx)
    logger.info("Welcome email | user=%s sent=%s", detail.get("user_id"), sent)


def handle_password_reset(detail: dict) -> None:
    """
    Stub — Cognito will provide the reset_link via a Custom Message Lambda trigger.
    Until Cognito is live this event won't be emitted; the handler is ready.
    """
    ctx = PasswordResetContext(
        to         = _addr(detail["email"]),
        first_name = detail.get("first_name"),
        reset_link = detail["reset_link"],
        expires_in = detail.get("expires_in", 3600),
    )
    sent = _provider().send_password_reset(ctx)
    logger.info("Password reset email | email=%s sent=%s", detail["email"], sent)


def handle_order_summary(detail: dict) -> None:
    ctx = OrderSummaryContext(
        to         = _addr(detail["email"], detail.get("first_name")),
        first_name = detail.get("first_name"),
        orders     = detail.get("orders", []),
    )
    sent = _provider().send_order_summary(ctx)
    logger.info("Order summary email | user=%s sent=%s", detail.get("user_id"), sent)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

_ROUTES = {
    PAYMENT_SETTLED:        handle_payment_settled,
    PAYMENT_FAILED:         handle_payment_failed,
    REFUND_COMPLETE:        handle_refund_complete,
    LOW_STOCK_DETECTED:     handle_low_stock,
    USER_REGISTERED:        handle_user_registered,
    PASSWORD_RESET_REQUEST: handle_password_reset,
    ORDER_SUMMARY_REQUEST:  handle_order_summary,
}


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    logger.info("Email event received: %s", event.get("detail-type", "unknown"))

    try:
        detail_type, detail = extract_detail(event)
    except ValueError as e:
        logger.error("Bad event shape: %s", e)
        return {"statusCode": 400, "body": str(e)}

    handler = _ROUTES.get(detail_type)
    if not handler:
        logger.warning("No handler for detail-type '%s'", detail_type)
        return {"statusCode": 400, "body": f"Unknown detail-type: {detail_type}"}

    try:
        handler(detail)
    except Exception:
        logger.exception("Unhandled error in email handler for '%s'", detail_type)
        # Re-raise so EventBridge retries (up to the rule's retry policy)
        raise

    return {"statusCode": 200, "body": "ok"}
