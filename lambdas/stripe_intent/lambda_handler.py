"""
lambdas/stripe_intent/lambda_handler.py

Invoked directly (RequestResponse) by payments_api via
STRIPE_INTENT_FUNCTION_ARN — not an API Gateway endpoint. Creates a Stripe
PaymentIntent and returns its id + client_secret.

This lambda's source didn't exist anywhere in what was shared with me —
template.yaml declared the StripeIntentFunction resource (with IAM/env vars
already wired up for it) but its CodeUri pointed at stripe_webhook's code
by mistake. Writing this from the contract payments_api already expects:

Input payload (from payments_api's _lambda.invoke Payload):
{
    "amount":         <int, amount in cents>,
    "currency":       <str, e.g. "cad">,
    "order_id":       <str>,
    "customer_email": <str, optional>
}

Output (read by payments_api as `stripe_result`):
{
    "intent_id":     <str>,
    "client_secret": <str>
}

On error, just let the exception propagate — Lambda sets FunctionError and
payments_api already handles that generically (treats any FunctionError as
a failed payment-intent creation and returns 500 to its caller).

Environment Variables:
  - STRIPE_SECRET_KEY_SECRET_NAME  Secrets Manager secret name holding the
                                    Stripe secret API key (sk_live_/sk_test_)
"""

import json
import logging
import os

import stripe
from secret_store import get_secret

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STRIPE_SECRET_KEY_SECRET_NAME = os.environ["STRIPE_SECRET_KEY_SECRET_NAME"]


def _configure_stripe() -> None:
    # get_secret() is lru_cached, so this is a no-op API call on warm invocations.
    stripe.api_key = get_secret(STRIPE_SECRET_KEY_SECRET_NAME)


def lambda_handler(event: dict, context) -> dict:
    logger.info("event=%s", json.dumps(event, default=str))

    amount = event.get("amount")
    currency = event.get("currency")
    order_id = event.get("order_id")
    customer_email = event.get("customer_email")

    missing = [k for k, v in (("amount", amount), ("currency", currency), ("order_id", order_id)) if not v]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}")

    _configure_stripe()

    logger.info("creating PaymentIntent | amount=%s currency=%s order_id=%s", amount, currency, order_id)
    intent = stripe.PaymentIntent.create(
        amount=int(amount),
        currency=currency,
        metadata={"order_id": str(order_id)},
        receipt_email=customer_email or None,
        description=f"ChonkyChonk order #{order_id}",
    )
    logger.info("PaymentIntent created | intent_id=%s", intent["id"])

    return {
        "intent_id": intent["id"],
        "client_secret": intent["client_secret"],
    }