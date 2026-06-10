"""
payments/lambda_handler.py

Handles charge and refund events from EventBridge.
After settlement it:
  1. Pushes result to frontend via WebSocket
  2. Emits PaymentSettled / PaymentFailed / RefundComplete onto EventBridge
     → picked up by the dedicated Email Lambda

  If the payment provider is completely unreachable (network/infra failure,
  not a declined card) it publishes to SNS chonkychonk-ops-alerts.

Environment Variables:
  - DB_CLUSTER_ARN
  - DB_SECRET_ARN
  - DB_NAME
  - PAYMENT_PROVIDER       default: stripe
  - APIGW_WS_ENDPOINT      API GW WebSocket management endpoint
  - EVENT_BUS_NAME         EventBridge bus (default: chonkychonk-bus)
  - SNS_OPS_TOPIC_ARN      SNS topic for payment-unreachable alerts
"""

import json
import logging
import os

import boto3
from botocore.exceptions import ClientError, EndpointResolutionError

from payments.models import ValidationError, parse_charge_payload, parse_refund_payload
from payments.providers.base import PaymentProvider, PaymentRequest, RefundRequest
from payments.providers.factory import DefaultPaymentProviderFactory
from payments.service import PaymentService
from shared.events import (
    SOURCE_PAYMENTS,
    PAYMENT_SETTLED,
    PAYMENT_FAILED,
    REFUND_COMPLETE,
    SNS_PAYMENT_UNREACHABLE,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DB_CLUSTER_ARN    = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN     = os.environ["DB_SECRET_ARN"]
DB_NAME           = os.environ["DB_NAME"]
DEFAULT_PROVIDER  = os.environ.get("PAYMENT_PROVIDER",  "stripe")
APIGW_WS_ENDPOINT = os.environ.get("APIGW_WS_ENDPOINT")
EVENT_BUS_NAME    = os.environ.get("EVENT_BUS_NAME",    "chonkychonk-bus")
SNS_OPS_TOPIC_ARN = os.environ.get("SNS_OPS_TOPIC_ARN")

_provider_factory = DefaultPaymentProviderFactory()
_events           = boto3.client("events")
_sns              = boto3.client("sns")


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def ok(body: dict, status: int = 200) -> dict:
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, default=str)}


def err(message: str, status: int = 400) -> dict:
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": message})}


# ---------------------------------------------------------------------------
# WebSocket notify
# ---------------------------------------------------------------------------

def notify_client(connection_id: str | None, payload: dict) -> None:
    if not connection_id or not APIGW_WS_ENDPOINT:
        return
    try:
        client = boto3.client("apigatewaymanagementapi", endpoint_url=APIGW_WS_ENDPOINT)
        client.post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(payload, default=str).encode("utf-8"),
        )
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "GoneException":
            logger.warning("WS connection gone: %s", connection_id)
        else:
            logger.error("WS notify failed: %s", e)


# ---------------------------------------------------------------------------
# EventBridge emit
# ---------------------------------------------------------------------------

def emit(detail_type: str, detail: dict) -> None:
    try:
        _events.put_events(Entries=[{
            "Source":       SOURCE_PAYMENTS,
            "DetailType":   detail_type,
            "Detail":       json.dumps(detail, default=str),
            "EventBusName": EVENT_BUS_NAME,
        }])
        logger.info("Emitted %s", detail_type)
    except ClientError as e:
        logger.error("Failed to emit %s: %s", detail_type, e)


# ---------------------------------------------------------------------------
# SNS ops alert — payment provider unreachable (not a declined card)
# ---------------------------------------------------------------------------

def alert_payment_unreachable(provider_name: str, order_id: int, error: str) -> None:
    if not SNS_OPS_TOPIC_ARN:
        logger.warning("SNS_OPS_TOPIC_ARN not set — skipping ops alert")
        return
    try:
        _sns.publish(
            TopicArn=SNS_OPS_TOPIC_ARN,
            Subject=f"[ChonkyChonk] Payment provider unreachable — order #{order_id}",
            Message=(
                f"Payment provider '{provider_name}' could not be reached "
                f"while processing order #{order_id}.\n\n"
                f"Error: {error}\n\n"
                f"The order has been saved but payment has NOT been collected. "
                f"Manual intervention may be required."
            ),
            MessageAttributes={
                "event_type": {"DataType": "String", "StringValue": SNS_PAYMENT_UNREACHABLE},
                "order_id":   {"DataType": "Number", "StringValue": str(order_id)},
            }
        )
        logger.info("SNS ops alert sent | order=%s", order_id)
    except ClientError as e:
        logger.error("Failed to send SNS alert: %s", e)


# ---------------------------------------------------------------------------
# Event extraction
# ---------------------------------------------------------------------------

def extract_payload(event: dict) -> dict:
    if "Records" in event:
        record = event["Records"][0]
        if "body" in record:
            return json.loads(record["body"])
        if "Sns" in record:
            return json.loads(record["Sns"]["Message"])
    if "detail" in event and "source" in event:
        return event["detail"]
    if "body" in event:
        body = event["body"]
        return json.loads(body) if isinstance(body, str) else body
    return event


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    logger.info("Payment event received")

    try:
        payload = extract_payload(event)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        return err(f"Malformed event: {e}", status=400)

    event_type    = payload.get("event_type", "").strip().lower()
    connection_id = payload.get("connection_id")

    if not event_type:
        return err("Missing required field: 'event_type' (charge | refund)")

    provider_name = payload.get("provider", DEFAULT_PROVIDER)
    try:
        provider: PaymentProvider = _provider_factory.get_provider(provider_name)
    except ValueError as e:
        return err(str(e), status=400)

    service = PaymentService(
        provider=provider,
        db_cluster_arn=DB_CLUSTER_ARN,
        db_secret_arn=DB_SECRET_ARN,
        db_name=DB_NAME,
    )

    if event_type == "charge":
        return _handle_charge(payload, service, provider_name, connection_id)

    if event_type == "refund":
        return _handle_refund(payload, service, connection_id)

    return err(f"Unknown event_type '{event_type}'. Expected: charge | refund")


# ---------------------------------------------------------------------------
# Charge
# ---------------------------------------------------------------------------

def _handle_charge(payload: dict, service: PaymentService,
                   provider_name: str, connection_id: str | None) -> dict:
    try:
        charge_payload = parse_charge_payload(payload)
    except (ValidationError, ValueError) as e:
        return err(str(e), status=422)

    request = PaymentRequest(
        order_id       = charge_payload.order_id,
        amount         = charge_payload.amount,
        currency       = charge_payload.currency,
        customer_email = charge_payload.customer_email,
        description    = charge_payload.description,
        metadata       = charge_payload.metadata,
    )

    try:
        result = service.process_charge(request)

    except Exception as e:
        # Provider was unreachable (connection error, timeout, etc.)
        # This is an infrastructure failure, not a card decline
        error_str = str(e)
        logger.exception("Payment provider unreachable | order=%s", charge_payload.order_id)

        alert_payment_unreachable(provider_name, charge_payload.order_id, error_str)

        notify_client(connection_id, {
            "event":    "payment_failed",
            "order_id": charge_payload.order_id,
            "message":  "Payment service temporarily unavailable. Our team has been alerted.",
        })
        emit(PAYMENT_FAILED, {
            "order_id":      charge_payload.order_id,
            "customer_email": charge_payload.customer_email,
            "customer_name": payload.get("customer_name"),
            "error_message": "Payment service temporarily unavailable. Please try again shortly.",
            "error_code":    "provider_unreachable",
        })
        return err("Payment provider unreachable", status=503)

    if result.success:
        notify_client(connection_id, {
            "event":                   "payment_complete",
            "order_id":                charge_payload.order_id,
            "status":                  result.status.value,
            "provider_transaction_id": result.provider_transaction_id,
            "amount":                  str(result.amount),
            "currency":                result.currency,
        })
        # Emit → Email Lambda sends confirmation
        emit(PAYMENT_SETTLED, {
            "order_id":                charge_payload.order_id,
            "customer_email":          charge_payload.customer_email,
            "customer_name":           payload.get("customer_name"),
            "amount":                  str(result.amount),
            "currency":                result.currency,
            "provider_transaction_id": result.provider_transaction_id,
            "subtotal":                payload.get("subtotal", "0.00"),
            "discount":                payload.get("discount", "0.00"),
            "tax":                     payload.get("tax", "0.00"),
            "shipping_fee":            payload.get("shipping_fee", "0.00"),
            "promotion_code":          payload.get("promotion_code"),
            "shipping_name":           payload.get("shipping_name", ""),
            "shipping_address":        payload.get("shipping_address", ""),
            "items":                   payload.get("items", []),
        })
        return ok({
            "event_type":              "charge",
            "success":                 True,
            "provider":                result.provider,
            "provider_transaction_id": result.provider_transaction_id,
            "status":                  result.status.value,
            "amount":                  str(result.amount),
            "currency":                result.currency,
        })

    # Card declined / payment rejected
    notify_client(connection_id, {
        "event":         "payment_failed",
        "order_id":      charge_payload.order_id,
        "error_code":    result.error_code,
        "error_message": result.error_message,
    })
    emit(PAYMENT_FAILED, {
        "order_id":       charge_payload.order_id,
        "customer_email": charge_payload.customer_email,
        "customer_name":  payload.get("customer_name"),
        "error_message":  result.error_message or "Payment declined.",
        "error_code":     result.error_code,
    })
    return ok({
        "event_type":    "charge",
        "success":       False,
        "provider":      result.provider,
        "status":        result.status.value,
        "error_code":    result.error_code,
        "error_message": result.error_message,
    }, status=402)


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------

def _handle_refund(payload: dict, service: PaymentService,
                   connection_id: str | None) -> dict:
    try:
        refund_payload = parse_refund_payload(payload)
    except (ValidationError, ValueError) as e:
        return err(str(e), status=422)

    request = RefundRequest(
        payment_id              = refund_payload.payment_id,
        provider_transaction_id = refund_payload.provider_transaction_id,
        amount                  = refund_payload.amount,
        reason                  = refund_payload.reason,
    )

    try:
        result = service.process_refund(request)
    except Exception:
        logger.exception("Unhandled error during refund")
        return err("Internal refund error", status=500)

    if result.success:
        notify_client(connection_id, {
            "event":     "refund_complete",
            "refund_id": result.refund_id,
            "status":    result.status.value,
            "amount":    str(result.amount),
            "currency":  result.currency,
        })
        emit(REFUND_COMPLETE, {
            "order_id":       payload.get("order_id"),
            "payment_id":     refund_payload.payment_id,
            "customer_email": payload.get("customer_email", ""),
            "customer_name":  payload.get("customer_name"),
            "refund_id":      result.refund_id,
            "amount":         str(result.amount),
            "currency":       result.currency,
        })
        return ok({
            "event_type": "refund",
            "success":    True,
            "provider":   result.provider,
            "refund_id":  result.refund_id,
            "status":     result.status.value,
            "amount":     str(result.amount),
            "currency":   result.currency,
        })

    return ok({
        "event_type":    "refund",
        "success":       False,
        "provider":      result.provider,
        "status":        result.status.value,
        "error_code":    result.error_code,
        "error_message": result.error_message,
    }, status=402)
