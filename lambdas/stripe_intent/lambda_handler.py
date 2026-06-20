import json
import logging
import os
from decimal import Decimal

import stripe

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]


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