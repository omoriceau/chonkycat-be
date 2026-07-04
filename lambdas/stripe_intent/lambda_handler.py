import json
import logging
import sys
import os
from decimal import Decimal

import stripe

# Add shared module to path for Lambda layer
from secrets import get_stripe_key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# Runs once on cold start, cached for warm invocations
stripe.api_key = get_stripe_key()

def lambda_handler(event, context):
    logger.info("stripe_intent event=%s", json.dumps(event, default=str))

    try:
        amount   = int(event["amount"])
        currency = str(event["currency"]).lower()
        order_id = str(event["order_id"])
        email    = str(event["customer_email"])

        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency=currency,
            automatic_payment_methods={"enabled": True},
            metadata={
                "order_id": order_id,
                "customer_email": email,
            }
        )

        logger.info("intent created: %s", intent.id)

        return {
            "intent_id":     intent.id,
            "client_secret": intent.client_secret,
        }

    except Exception as e:
        logger.exception("Stripe error")
        raise