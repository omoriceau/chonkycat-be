"""
email_service/lambda_handler.py

Generic EventBridge email handler for all email events.
Handles emails triggered by various services via EventBridge.

Currently supports:
- OrderCreated (from orders service)
- OrderFailure (from payments service)
- LowStockDetected (from orders service)

Can be extended for other email types.

Environment Variables:
- ENVIRONMENT: dev or prod (default: dev)
- DEV_EMAIL: Email to redirect all dev emails to (for SES Sandbox testing)
"""

import json
import logging
import os

from email_service.factory import DefaultEmailProviderFactory
from email_service.base import OrderConfirmationEmail, OrderFailureEmail

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
            if detail_type == "OrderCreated":
                return handle_order_created(detail)
            elif detail_type == "OrderFailure":
                return handle_order_failure(detail)
            elif detail_type == "LowStockDetected":
                logger.info("Low stock detected | products=%s", len(detail.get("products", [])))
                return {"statusCode": 200, "body": json.dumps({"message": "Low stock event received"})}
        
        logger.warning("Unknown event | source=%s detail_type=%s", source, detail_type)
        return {"statusCode": 400, "body": json.dumps({"error": "Unknown event type"})}
    
    except Exception as e:
        logger.exception("Error processing email event")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def handle_order_created(detail: dict) -> dict:
    """Send order confirmation email."""
    logger.info("Handling OrderCreated event | order_id=%s", detail.get("order_id"))
    
    try:
        customer_email = detail.get("customer_email")
        order_id = detail.get("order_id")
        
        if not customer_email or not order_id:
            logger.warning("Missing required fields in OrderCreated event")
            return {"statusCode": 400, "body": json.dumps({"error": "Missing customer_email or order_id"})}
        
        # Get actual recipient email (may be redirected in dev mode)
        email_to, subject_prefix = get_recipient_email(customer_email, f"order {order_id}")
        
        # Build the email with all order details
        email = OrderConfirmationEmail(
            to=email_to,
            order_id=order_id,
            subtotal=detail.get("subtotal"),
            discount=detail.get("discount"),
            tax=detail.get("tax"),
            shipping_fee=detail.get("shipping_fee"),
            total=detail.get("amount"),
            currency=detail.get("currency", "CAD"),
            items=detail.get("items", []),
            shipping_name=detail.get("shipping_name"),
            shipping_address=detail.get("shipping_address"),
            promotion_code=detail.get("promotion_code"),
        )
        
        # Add subject prefix if in dev mode
        if subject_prefix and hasattr(email, 'subject'):
            email.subject = subject_prefix + email.subject
        
        # Send via SES
        provider = DefaultEmailProviderFactory().get_provider("ses")
        success = provider.send_order_confirmation(email)
        
        if success:
            logger.info("Order confirmation email sent | to=%s order_id=%s original=%s", email_to, order_id, customer_email)
            return {"statusCode": 200, "body": json.dumps({"message": "Email sent", "order_id": order_id, "sent_to": email_to})}
        else:
            logger.error("Failed to send order confirmation email | order_id=%s", order_id)
            return {"statusCode": 500, "body": json.dumps({"error": "Failed to send email"})}
    
    except Exception as e:
        logger.exception("Error sending order confirmation email")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def handle_order_failure(detail: dict) -> dict:
    """Send order failure email."""
    logger.info("Handling OrderFailure event | order_id=%s", detail.get("order_id"))
    
    try:
        customer_email = detail.get("customer_email")
        order_id = detail.get("order_id")
        reason = detail.get("reason", "Unknown error")
        
        if not customer_email or not order_id:
            logger.warning("Missing required fields in OrderFailure event")
            return {"statusCode": 400, "body": json.dumps({"error": "Missing customer_email or order_id"})}
        
        # Get actual recipient email (may be redirected in dev mode)
        email_to, subject_prefix = get_recipient_email(customer_email, f"order {order_id}")
        
        # Build the email
        email = OrderFailureEmail(
            to=email_to,
            order_id=order_id,
            reason=reason,
        )
        
        # Add subject prefix if in dev mode
        if subject_prefix and hasattr(email, 'subject'):
            email.subject = subject_prefix + email.subject
        
        # Send via SES
        provider = DefaultEmailProviderFactory().get_provider("ses")
        success = provider.send_order_failure(email)
        
        if success:
            logger.info("Order failure email sent | to=%s order_id=%s original=%s", email_to, order_id, customer_email)
            return {"statusCode": 200, "body": json.dumps({"message": "Email sent", "order_id": order_id, "sent_to": email_to})}
        else:
            logger.error("Failed to send order failure email | order_id=%s", order_id)
            return {"statusCode": 500, "body": json.dumps({"error": "Failed to send email"})}
    
    except Exception as e:
        logger.exception("Error sending order failure email")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
