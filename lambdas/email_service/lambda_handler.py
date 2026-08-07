"""
email_service/lambda_handler.py

Generic EventBridge email handler for all email events.
Handles emails triggered by various services via EventBridge.

Currently supports:
- PaymentSucceeded (from stripe_webhook) — order confirmation email
- PaymentFailed (from stripe_webhook) — order failure email
- LowStockDetected (from orders service)
- UserCreated (from users service) — sends a welcome email

Order confirmation/failure emails fire off Stripe's webhook confirming the
charge actually succeeded or failed, not off order creation — an order is
only "pending" at creation time, before the shopper has even paid, so
sending a confirmation then would tell them something happened before it
actually did. See stripe_webhook/lambda_handler.py's
handle_payment_intent_succeeded/_failed for where these are emitted, and
_order_email_fields() there for how the order/item detail is attached
(this handler never touches DynamoDB itself — the payload already has
everything it needs).

Can be extended for other email types.

Environment Variables:
- ENVIRONMENT: dev or prod (default: dev)
- DEV_EMAIL: Email to redirect all dev emails to (for SES Sandbox testing)
"""

import json
import logging
import os

from email_service.factory import DefaultEmailProviderFactory
from email_service.base import EmailAddress, OrderConfirmationEmail, OrderFailureEmail, WelcomeEmail
from email_service.ses_provider import SUPPORT_EMAIL

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Dev mode configuration
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
DEV_EMAIL = os.environ.get("DEV_EMAIL")
IS_DEV = ENVIRONMENT == "dev"


def get_recipient_email(original_email: str, context_info: str = "") -> tuple[str, str]:
    """
    Get the actual email to send to, with optional subject prefix for dev mode.
    
    Args:
        original_email: The intended recipient email
        context_info: Additional context (e.g., customer email) to include in dev prefix
    
    Returns:
        Tuple of (email_to_use, subject_prefix)
    """
    if IS_DEV and DEV_EMAIL:
        prefix = f"[DEV: {context_info}] " if context_info else "[DEV] "
        logger.info("Dev mode: redirecting email from %s to %s | prefix=%s", original_email, DEV_EMAIL, prefix)
        return DEV_EMAIL, prefix
    return original_email, ""


def lambda_handler(event, context):
    """
    Generic email handler for EventBridge events.
    Routes to specific handlers based on source and detail-type.
    """
    logger.info("Email handler triggered | event=%s", json.dumps(event, default=str)[:200])
    
    try:
        source = event.get("source", "")
        detail_type = event.get("detail-type", "")
        detail = event.get("detail", {})
        
        # Route based on source and detail-type
        if source == "chonkychonk.orders":
            if detail_type == "LowStockDetected":
                logger.info("Low stock detected | products=%s", len(detail.get("products", [])))
                return {"statusCode": 200, "body": json.dumps({"message": "Low stock event received"})}

        elif source == "chonkychonk.payments":
            if detail_type == "PaymentSucceeded":
                return handle_payment_succeeded(detail)
            elif detail_type == "PaymentFailed":
                return handle_payment_failed(detail)

        elif source == "chonkychonk.users":
            if detail_type == "UserCreated":
                return handle_user_created(detail)

        logger.warning("Unknown event | source=%s detail_type=%s", source, detail_type)
        return {"statusCode": 400, "body": json.dumps({"error": "Unknown event type"})}
    
    except Exception as e:
        logger.exception("Error processing email event")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def handle_payment_succeeded(detail: dict) -> dict:
    """Send order confirmation email once payment has actually succeeded."""
    logger.info("Handling PaymentSucceeded event | order_id=%s", detail.get("order_id"))

    try:
        customer_email = detail.get("customer_email")
        order_id = detail.get("order_id")

        if not customer_email or not order_id:
            logger.warning("Missing required fields in PaymentSucceeded event")
            return {"statusCode": 400, "body": json.dumps({"error": "Missing customer_email or order_id"})}
        
        # Get actual recipient email (may be redirected in dev mode)
        email_to, subject_prefix = get_recipient_email(customer_email, f"order {order_id}")
        
        # Build the email with all order details
        email = OrderConfirmationEmail(
            to=EmailAddress(address=email_to, name=detail.get("shipping_name")),
            order_id=order_id,
            subtotal=detail.get("subtotal"),
            discount=detail.get("discount"),
            tax=detail.get("tax"),
            shipping_fee=detail.get("shipping_fee"),
            total_amount=detail.get("amount"),
            currency=detail.get("currency", "CAD"),
            items=detail.get("items", []),
            shipping_name=detail.get("shipping_name"),
            shipping_address=detail.get("shipping_address"),
            promotion_code=detail.get("promotion_code"),
            customer_notes=detail.get("customer_notes"),
        )

        # Send via SES
        provider = DefaultEmailProviderFactory().get_provider("ses")
        success = provider.send_order_confirmation(email, subject_prefix=subject_prefix)
        
        if success:
            logger.info("Order confirmation email sent | to=%s order_id=%s original=%s", email_to, order_id, customer_email)
            return {"statusCode": 200, "body": json.dumps({"message": "Email sent", "order_id": order_id, "sent_to": email_to})}
        else:
            logger.error("Failed to send order confirmation email | order_id=%s", order_id)
            return {"statusCode": 500, "body": json.dumps({"error": "Failed to send email"})}
    
    except Exception as e:
        logger.exception("Error sending order confirmation email")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def handle_user_created(detail: dict) -> dict:
    """Send welcome email to a newly created user."""
    logger.info("Handling UserCreated event | user_id=%s", detail.get("user_id"))

    try:
        user_email = detail.get("email")
        user_id = detail.get("user_id")

        if not user_email or not user_id:
            logger.warning("Missing required fields in UserCreated event")
            return {"statusCode": 400, "body": json.dumps({"error": "Missing email or user_id"})}

        first_name = detail.get("first_name")
        role = detail.get("role", "customer")

        # Get actual recipient email (may be redirected in dev mode)
        email_to, subject_prefix = get_recipient_email(user_email, f"user {user_id}")

        email = WelcomeEmail(
            to=EmailAddress(address=email_to, name=first_name),
            first_name=first_name,
            role=role,
        )

        provider = DefaultEmailProviderFactory().get_provider("ses")
        success = provider.send_welcome_email(email, subject_prefix=subject_prefix)

        if success:
            logger.info("Welcome email sent | to=%s user_id=%s original=%s", email_to, user_id, user_email)
            return {"statusCode": 200, "body": json.dumps({"message": "Email sent", "user_id": user_id, "sent_to": email_to})}
        else:
            logger.error("Failed to send welcome email | user_id=%s", user_id)
            return {"statusCode": 500, "body": json.dumps({"error": "Failed to send email"})}

    except Exception as e:
        logger.exception("Error sending welcome email")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def handle_payment_failed(detail: dict) -> dict:
    """Send order failure email once payment has actually failed."""
    logger.info("Handling PaymentFailed event | order_id=%s", detail.get("order_id"))

    try:
        customer_email = detail.get("customer_email")
        order_id = detail.get("order_id")
        reason = detail.get("reason", "Unknown error")

        if not customer_email or not order_id:
            logger.warning("Missing required fields in PaymentFailed event")
            return {"statusCode": 400, "body": json.dumps({"error": "Missing customer_email or order_id"})}
        
        # Get actual recipient email (may be redirected in dev mode)
        email_to, subject_prefix = get_recipient_email(customer_email, f"order {order_id}")
        
        # Build the email
        email = OrderFailureEmail(
            to=EmailAddress(address=email_to),
            order_id=order_id,
            error_message=reason,
            support_email=SUPPORT_EMAIL,
        )

        # Send via SES
        provider = DefaultEmailProviderFactory().get_provider("ses")
        success = provider.send_order_failure(email, subject_prefix=subject_prefix)
        
        if success:
            logger.info("Order failure email sent | to=%s order_id=%s original=%s", email_to, order_id, customer_email)
            return {"statusCode": 200, "body": json.dumps({"message": "Email sent", "order_id": order_id, "sent_to": email_to})}
        else:
            logger.error("Failed to send order failure email | order_id=%s", order_id)
            return {"statusCode": 500, "body": json.dumps({"error": "Failed to send email"})}
    
    except Exception as e:
        logger.exception("Error sending order failure email")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
