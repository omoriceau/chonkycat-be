"""
payments/service.py

PaymentService orchestrates the full payment lifecycle:
  - delegates charging/refunding to the injected provider
  - persists results to Aurora via RDS Data API
  - owns no Stripe-specific logic
"""

import logging
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

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


class PaymentService:

    def __init__(
        self,
        provider: PaymentProvider,
        db_cluster_arn: str,
        db_secret_arn: str,
        db_name: str,
        rds_client=None,
    ):
        self._provider       = provider
        self._cluster_arn    = db_cluster_arn
        self._secret_arn     = db_secret_arn
        self._db_name        = db_name
        self._rds            = rds_client or boto3.client("rds-data")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_charge(self, request: PaymentRequest) -> PaymentResult:
        """
        Charge via the provider, then persist the result to payments table.
        The order row must already exist before calling this.
        """
        logger.info(
            "Initiating charge | order=%s amount=%s %s provider=%s",
            request.order_id, request.amount, request.currency, self._provider.provider_name,
        )

        result = self._provider.charge(request)

        logger.info(
            "Charge result | order=%s success=%s tx=%s status=%s",
            request.order_id, result.success, result.provider_transaction_id, result.status,
        )

        self._persist_payment(request, result)

        if result.success:
            self._update_order_status(request.order_id, "processing")

        return result

    def process_refund(self, request: RefundRequest) -> RefundResult:
        """
        Refund via the provider, then persist the result to refunds table.
        """
        logger.info(
            "Initiating refund | payment=%s tx=%s amount=%s",
            request.payment_id, request.provider_transaction_id, request.amount,
        )

        result = self._provider.refund(request)

        logger.info(
            "Refund result | payment=%s success=%s refund_id=%s",
            request.payment_id, result.success, result.refund_id,
        )

        self._persist_refund(request, result)

        if result.success:
            self._update_order_status_by_payment(request.payment_id, "refunded")

        return result

    # ------------------------------------------------------------------
    # DB persistence
    # ------------------------------------------------------------------

    def _persist_payment(self, request: PaymentRequest, result: PaymentResult) -> None:
        sql = """
            INSERT INTO payments
              (order_id, payment_provider, provider_transaction_id,
               method, amount, currency, status, paid_at)
            VALUES
              (:order_id, :provider, :tx_id,
               :method, :amount, :currency, :status,
               CASE WHEN :paid THEN NOW() ELSE NULL END)
        """
        params = [
            {"name": "order_id", "value": {"longValue":   request.order_id}},
            {"name": "provider", "value": {"stringValue": result.provider}},
            {"name": "tx_id",    "value": {"stringValue": result.provider_transaction_id or ""}},
            {"name": "method",   "value": {"stringValue": "credit_card"}},   # extend if needed
            {"name": "amount",   "value": {"stringValue": str(result.amount)}},
            {"name": "currency", "value": {"stringValue": result.currency}},
            {"name": "status",   "value": {"stringValue": result.status.value}},
            {"name": "paid",     "value": {"booleanValue": result.status == PaymentStatus.PAID}},
        ]
        self._execute(sql, params, label="persist_payment")

    def _persist_refund(self, request: RefundRequest, result: RefundResult) -> None:
        sql = """
            INSERT INTO refunds (payment_id, amount, reason, status)
            VALUES (:payment_id, :amount, :reason, :status)
        """
        params = [
            {"name": "payment_id", "value": {"longValue":   request.payment_id}},
            {"name": "amount",     "value": {"stringValue": str(result.amount)}},
            {"name": "reason",     "value": {"stringValue": request.reason or ""}},
            {"name": "status",     "value": {"stringValue": result.status.value}},
        ]
        self._execute(sql, params, label="persist_refund")

    def _update_order_status(self, order_id: int, status: str) -> None:
        sql = "UPDATE orders SET status = :status WHERE id = :order_id"
        params = [
            {"name": "status",   "value": {"stringValue": status}},
            {"name": "order_id", "value": {"longValue":   order_id}},
        ]
        self._execute(sql, params, label="update_order_status")

    def _update_order_status_by_payment(self, payment_id: int, status: str) -> None:
        sql = """
            UPDATE orders o
            JOIN   payments p ON p.order_id = o.id
            SET    o.status = :status
            WHERE  p.id = :payment_id
        """
        params = [
            {"name": "status",     "value": {"stringValue": status}},
            {"name": "payment_id", "value": {"longValue":   payment_id}},
        ]
        self._execute(sql, params, label="update_order_status_by_payment")

    def _execute(self, sql: str, params: list, label: str = "") -> dict:
        try:
            return self._rds.execute_statement(
                resourceArn=self._cluster_arn,
                secretArn=self._secret_arn,
                database=self._db_name,
                sql=sql,
                parameters=params,
            )
        except ClientError as e:
            logger.error("DB error [%s]: %s", label, e)
            raise
