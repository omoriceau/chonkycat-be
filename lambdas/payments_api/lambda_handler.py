import boto3

print('secretsmanager')

client = boto3.client("secretsmanager")

print(client.list_secrets(MaxResults=1))

print('events')
client = boto3.client("events")

print(client.list_event_buses())

print('lambda')
client = boto3.client("lambda")

resp = client.list_functions(MaxItems=1)

print(resp)


"""
lambdas/payments_api/lambda_handler.py
"""

import json
import logging
import os
from decimal import Decimal

import boto3
import stripe
from db import get_db_client

from botocore.exceptions import ClientError
from botocore.config import Config

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "chonkychonk-bus")
STRIPE_SECRET_KEY = os.environ["STRIPE_SECRET_KEY"]
STRIPE_INTENT_ARN     = os.environ["STRIPE_INTENT_FUNCTION_ARN"]  # <-- add this


stripe.api_key = STRIPE_SECRET_KEY

_events = boto3.client("events")
_lambda = boto3.client("lambda", config=Config(
    connect_timeout=5,
    read_timeout=25,
    retries={"max_attempts": 0}
))


def lambda_handler(event, context):
    logger.info(">>>>event=%s", json.dumps(event, default=str))
    try:
        sts = boto3.client("sts")
        result = sts.get_caller_identity()

        return {
            "statusCode": 200,
            "body": str(result)
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "body": str(e)
        }

# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def ok(body: dict, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }


def err(message: str, status: int = 400) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({"error": message}),
    }


# # ---------------------------------------------------------------------------
# # Cart validation
# # ---------------------------------------------------------------------------

# def validate_cart(body: dict):
#     items = body.get("items")

#     if not items or not isinstance(items, list):
#         raise ValueError("Cart is empty or invalid")

#     cleaned = []

#     for item in items:
#         product_id = item.get("product_id")
#         quantity = item.get("quantity")

#         if product_id is None:
#             raise ValueError("Missing product_id")

#         if quantity is None or int(quantity) <= 0:
#             raise ValueError(f"Invalid quantity for product {product_id}")

#         cleaned.append({
#             "product_id": int(product_id),
#             "quantity": int(quantity),
#         })

#     return cleaned


# # ---------------------------------------------------------------------------
# # Product / inventory layer
# # ---------------------------------------------------------------------------

# def load_products(product_ids):
#     if not product_ids:
#         return {}

#     logger.info("load_products: connecting to DB")
#     db = get_db_client()
#     logger.info("load_products: connected, querying product_ids=%s", product_ids)

#     rows = db.fetch_all(
#         """
#         SELECT id, name, price, qty
#         FROM products
#         WHERE id = ANY(%s)
#         """,
#         (product_ids,)
#     )

#     logger.info("load_products: got %d row(s)", len(rows))

#     result = {}
#     for r in rows:
#         result[r["id"]] = {
#             "id": r["id"],
#             "name": r["name"],
#             "price": Decimal(str(r["price"])),
#             "qty": r["qty"],
#         }

#     return result


# def get_or_create_user(db, email: str) -> int:
#     logger.info("get_or_create_user: email=%s", email)

#     row = db.fetch_one(
#         """
#         INSERT INTO users (email)
#         VALUES (%s)
#         ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
#         RETURNING id
#         """,
#         (email,)
#     )

#     if not row:
#         raise Exception(f"Could not get or create user for email: {email}")

#     logger.info("get_or_create_user: user_id=%s", row["id"])
#     return row["id"]


# def create_order_pending(items, total, email):
#     logger.info("create_order_pending: email=%s total=%s items=%s", email, total, items)

#     db = get_db_client()
#     logger.info("create_order_pending: DB connected")

#     user_id = get_or_create_user(db, email)

#     logger.info("create_order_pending: inserting order for user_id=%s", user_id)
#     order_row = db.fetch_one(
#         """
#         INSERT INTO orders (
#             user_id,
#             status,
#             subtotal,
#             tax_amount,
#             shipping_amount,
#             total_amount
#         )
#         VALUES (
#             %s,
#             'pending_payment',
#             %s,
#             0,
#             0,
#             %s
#         )
#         RETURNING id
#         """,
#         (user_id, total, total)
#     )

#     if not order_row:
#         raise Exception("Failed to create order")

#     order_id = order_row["id"]
#     logger.info("create_order_pending: order_id=%s", order_id)

#     for item in items:
#         logger.info("create_order_pending: inserting order_item product_id=%s qty=%s", item["product_id"], item["quantity"])
#         db.execute(
#             """
#             INSERT INTO order_items (
#                 order_id,
#                 product_id,
#                 quantity,
#                 unit_price,
#                 line_total,
#                 name_snapshot
#             )
#             SELECT
#                 %s,
#                 p.id,
#                 %s,
#                 p.price,
#                 (p.price * %s),
#                 p.name
#             FROM products p
#             WHERE p.id = %s
#             """,
#             (
#                 order_id,
#                 item["quantity"],
#                 item["quantity"],
#                 item["product_id"]
#             )
#         )
#         logger.info("create_order_pending: inserted order_item product_id=%s", item["product_id"])

#     logger.info("create_order_pending: done, returning order_id=%s", order_id)
#     return order_id


# # ---------------------------------------------------------------------------
# # EventBridge
# # ---------------------------------------------------------------------------

# def emit_event(detail_type: str, detail: dict):
#     try:
#         logger.info("emit_event: detail_type=%s detail=%s", detail_type, detail)
#         _events.put_events(Entries=[{
#             "Source": "chonkychonk.payments",
#             "DetailType": detail_type,
#             "Detail": json.dumps(detail, default=str),
#             "EventBusName": EVENT_BUS_NAME,
#         }])
#         logger.info("emit_event: success")
#     except ClientError as e:
#         logger.exception("EventBridge failure: %s", e)

# def create_stripe_intent(amount: int, currency: str, order_id: int, email: str) -> dict:
#     logger.info("invoking StripeIntentFunction: amount=%s currency=%s order_id=%s", amount, currency, order_id)

#     response = _lambda.invoke(
#         FunctionName=STRIPE_INTENT_ARN,
#         InvocationType="RequestResponse",
#         Payload=json.dumps({
#             "amount":         amount,
#             "currency":       currency,
#             "order_id":       order_id,
#             "customer_email": email,
#         }),
#     )

#     logger.info("invoke returned: status=%s function_error=%s", response["StatusCode"], response.get("FunctionError"))
#     payload = json.loads(response["Payload"].read())
#     logger.info("invoke payload: %s", payload)

#     if response.get("FunctionError"):
#         logger.error("StripeIntentFunction error: %s", payload)
#         raise Exception(f"Stripe Lambda failed: {payload}")

#     return payload
    
# # ---------------------------------------------------------------------------
# # Core handler
# # ---------------------------------------------------------------------------

# def lambda_handler(event, context):
#     logger.info("event=%s", json.dumps(event, default=str))

#     body = event.get("body", "")
#     logger.info("raw body type=%s value=%s", type(body).__name__, repr(body))

#     if body is None:
#         logger.warning("body is None, defaulting to empty dict")
#         body = {}
#     elif isinstance(body, str):
#         logger.info("body is string, attempting JSON parse")
#         try:
#             body = json.loads(body)
#             logger.info("body parsed OK: %s", body)
#         except json.JSONDecodeError as e:
#             logger.error("body JSON parse failed: %s", e)
#             return err("Invalid JSON body")
#     elif isinstance(body, dict):
#         logger.info("body is already a dict, using as-is")
#     else:
#         logger.error("unexpected body type: %s", type(body).__name__)
#         return err("Invalid request body")

#     logger.info("final body=%s", body)

#     try:
#         logger.info("validating cart")
#         items = validate_cart(body)
#         currency = str(body.get("currency", "CAD")).lower()
#         email = body.get("customer_email", "")
#         logger.info("cart valid: items=%s currency=%s email=%s", items, currency, email)

#         product_ids = [i["product_id"] for i in items]
#         logger.info("loading products: product_ids=%s", product_ids)
#         products = load_products(product_ids)
#         logger.info("products loaded: %s", list(products.keys()))

#         total = Decimal("0")

#         for item in items:
#             pid = item["product_id"]
#             qty = item["quantity"]

#             if pid not in products:
#                 logger.warning("product not found: pid=%s", pid)
#                 return err(f"Product not found: {pid}", 404)

#             product = products[pid]
#             logger.info("product=%s qty_available=%s qty_requested=%s", product["name"], product["qty"], qty)

#             if product["qty"] < qty:
#                 logger.warning("insufficient inventory: product=%s available=%s requested=%s", product["name"], product["qty"], qty)
#                 return err(f"Insufficient inventory for {product['name']}", 409)

#             total += product["price"] * qty

#         logger.info("total computed: %s %s", total, currency)

#         logger.info("creating order")
#         order_id = create_order_pending(items, total, email)
#         logger.info("order created: order_id=%s", order_id)

#         logger.info("before invoking StripeIntentFunction: amount=%s currency=%s order_id=%s", int(total * 100), currency, order_id)
#         stripe_result = create_stripe_intent(
#             amount=int(total * 100),
#             currency=currency,
#             order_id=order_id,
#             email=email,
#         )
#         logger.info("stripe result: %s", stripe_result)

#         emit_event("PaymentIntentCreated", {
#             "order_id": order_id,
#             "amount": str(total),
#             "currency": currency,
#             "stripe_payment_intent": stripe_result["intent_id"],
#         })

#         return ok({
#             "order_id": order_id,
#             "client_secret": stripe_result["client_secret"],
#         })

#     except ValueError as e:
#         logger.error("ValueError: %s", e)
#         return err(str(e), status=422)

#     except Exception as e:
#         logger.exception("Unhandled error: %s", e)
#         return err("Internal server error", status=500)
#     logger.info("event=%s", json.dumps(event, default=str))

#     body = event.get("body", "")
#     if isinstance(body, str):
#         try:
#             body = json.loads(body)
#         except json.JSONDecodeError:
#             return err("Invalid JSON body")

#     try:
#         logger.info("validating cart")
#         items = validate_cart(body)
#         currency = str(body.get("currency", "CAD")).lower()
#         email = body.get("customer_email", "")
#         logger.info("cart valid: items=%s currency=%s email=%s", items, currency, email)

#         product_ids = [i["product_id"] for i in items]
#         logger.info("loading products: product_ids=%s", product_ids)
#         products = load_products(product_ids)
#         logger.info("products loaded: %s", list(products.keys()))

#         total = Decimal("0")

#         for item in items:
#             pid = item["product_id"]
#             qty = item["quantity"]

#             if pid not in products:
#                 return err(f"Product not found: {pid}", 404)

#             product = products[pid]

#             if product["qty"] < qty:
#                 return err(f"Insufficient inventory for {product['name']}", 409)

#             total += product["price"] * qty

#         logger.info("total computed: %s %s", total, currency)

#         logger.info("creating order")
#         order_id = create_order_pending(items, total, email)
#         logger.info("order created: order_id=%s", order_id)

#         logger.info("creating Stripe PaymentIntent: amount=%s currency=%s", int(total * 100), currency)
#         stripe_result = create_stripe_intent(
#             amount=int(total * 100),
#             currency=currency,
#             order_id=order_id,
#             email=email,
#         )
#         logger.info("Stripe PaymentIntent created: intent_id=%s", intent.id)

#         emit_event("PaymentIntentCreated", {
#             "order_id": order_id,
#             "amount": str(total),
#             "currency": currency,
#             "stripe_payment_intent": stripe_result["client_secret"]
#         })

#         return ok({
#             "order_id": order_id,
#             "client_secret": stripe_result["client_secret"]
#         }, status=200)

#     except ValueError as e:
#         return err(str(e), status=422)

#     except Exception as e:
#         logger.exception("Unhandled error")
#         return err("Internal server error", status=500)