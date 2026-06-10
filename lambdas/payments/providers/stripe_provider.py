"""
payments/providers/stripe_provider.py

Stripe implementation of PaymentProvider.
Requires:  pip install stripe
Env vars:
  - STRIPE_SECRET_KEY   Live or test secret key
  - STRIPE_API_VERSION  (optional, defaults to current)
"""

import logging
import os
from decimal import Decimal

import stripe
from stripe.error import StripeError

from payments.providers.base import (
    PaymentProvider,
    PaymentRequest,
    PaymentResult,
    PaymentStatus,
    RefundRequest,
    RefundResult,
    RefundStatus,
)

logger = logging.getLogger(__name__)


def _to_stripe_amount(amount: Decimal, currency: str) -> int:
    """
    Stripe expects amounts in the smallest currency unit (cents for CAD/USD).
    Extend this mapping for zero-decimal currencies (JPY, KRW, etc.) as needed.
    """
    ZERO_DECIMAL_CURRENCIES = {"bif", "clp", "gnf", "jpy", "kmf", "krw",
                                "mga", "pyg", "rwf", "ugx", "vnd", "vuv",
                                "xaf", "xof", "xpf"}
    if currency.lower() in ZERO_DECIMAL_CURRENCIES:
        return int(amount)
    return int(amount * 100)


class StripeProvider(PaymentProvider):

    def __init__(self, secret_key: str | None = None, api_version: str | None = None):
        stripe.api_key = secret_key or os.environ["STRIPE_SECRET_KEY"]
        if api_version:
            stripe.api_version = api_version

    # ------------------------------------------------------------------
    @property
    def provider_name(self) -> str:
        return "Stripe"

    # ------------------------------------------------------------------
    def charge(self, request: PaymentRequest) -> PaymentResult:
        try:
            intent = stripe.PaymentIntent.create(
                amount=_to_stripe_amount(request.amount, request.currency),
                currency=request.currency.lower(),
                receipt_email=request.customer_email,
                description=request.description or f"Order #{request.order_id}",
                metadata={
                    "order_id": str(request.order_id),
                    **request.metadata,
                },
                # Confirm immediately for server-side initiated payments.
                # For client-side flows, remove confirm=True and return the
                # client_secret for the frontend to complete.
                confirm=True,
                automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
            )

            status = (
                PaymentStatus.PAID
                if intent.status == "succeeded"
                else PaymentStatus.PENDING
            )

            return PaymentResult(
                success=intent.status == "succeeded",
                provider=self.provider_name,
                provider_transaction_id=intent.id,
                status=status,
                amount=request.amount,
                currency=request.currency,
                raw_response=dict(intent),
            )

        except StripeError as e:
            logger.error("Stripe charge failed: %s", e.user_message, exc_info=True)
            return PaymentResult(
                success=False,
                provider=self.provider_name,
                provider_transaction_id="",
                status=PaymentStatus.FAILED,
                amount=request.amount,
                currency=request.currency,
                error_code=e.code,
                error_message=e.user_message,
                raw_response=e.json_body,
            )

    # ------------------------------------------------------------------
    def refund(self, request: RefundRequest) -> RefundResult:
        try:
            refund = stripe.Refund.create(
                payment_intent=request.provider_transaction_id,
                amount=_to_stripe_amount(request.amount, "CAD"),  # currency stored on intent
                reason="requested_by_customer",
                metadata={"reason": request.reason, "payment_id": str(request.payment_id)},
            )

            status = (
                RefundStatus.REFUNDED
                if refund.status == "succeeded"
                else RefundStatus.PENDING
            )

            return RefundResult(
                success=refund.status == "succeeded",
                provider=self.provider_name,
                refund_id=refund.id,
                status=status,
                amount=Decimal(refund.amount) / 100,
                currency=refund.currency.upper(),
                raw_response=dict(refund),
            )

        except StripeError as e:
            logger.error("Stripe refund failed: %s", e.user_message, exc_info=True)
            return RefundResult(
                success=False,
                provider=self.provider_name,
                refund_id="",
                status=RefundStatus.FAILED,
                amount=request.amount,
                currency="",
                error_code=e.code,
                error_message=e.user_message,
                raw_response=e.json_body,
            )
