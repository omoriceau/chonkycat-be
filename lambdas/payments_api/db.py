"""
DynamoDB helper for the payments_api Lambda.

Replaces the old psycopg2/RDS client. Auth is via the Lambda's execution
role (IAM) — no secrets/passwords needed.

Touches THREE tables:
  orders    (aws_dynamodb_table.orders)    — read only (status/total/user_id)
  users     (aws_dynamodb_table.users)     — read only (email)
  payments  (aws_dynamodb_table.payments)  — write (new payment record)

NOTE: the original code never wrote to a payments table at all (grepped —
zero hits). The `payments` table's ProviderTxnIndex GSI was declared in
terraform for "the (future) Stripe webhook handler" but nothing populated
it. This client adds that write so the index is actually useful — see the
matching change in stripe_webhook/db.py.
"""

import os
from datetime import datetime, timezone

import boto3


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class DynamoDBClient:

    def __init__(self):
        self.orders_table_name = os.environ.get("ORDERS_TABLE_NAME")
        self.users_table_name = os.environ.get("USERS_TABLE_NAME")
        self.payments_table_name = os.environ.get("PAYMENTS_TABLE_NAME")

        missing = [
            name for name, val in (
                ("ORDERS_TABLE_NAME", self.orders_table_name),
                ("USERS_TABLE_NAME", self.users_table_name),
                ("PAYMENTS_TABLE_NAME", self.payments_table_name),
            ) if not val
        ]
        if missing:
            raise RuntimeError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                f"Set these to the table names output by terraform."
            )

        region = os.environ.get("AWS_REGION")
        resource = boto3.resource("dynamodb", region_name=region)
        self.orders_table = resource.Table(self.orders_table_name)
        self.users_table = resource.Table(self.users_table_name)
        self.payments_table = resource.Table(self.payments_table_name)

    def get_order(self, order_id: str) -> dict | None:
        resp = self.orders_table.get_item(Key={"order_id": order_id, "sk": "ORDER"})
        order = resp.get("Item")
        if order and order.get("deleted_at"):
            return None
        return order

    def get_user_email(self, user_id: str) -> str | None:
        resp = self.users_table.get_item(Key={"user_id": user_id})
        item = resp.get("Item")
        return item["email"] if item else None

    def create_payment_record(
        self, order_id: str, intent_id: str, status: str, amount: str, currency: str
    ) -> None:
        now = _now_iso()
        self.payments_table.put_item(Item={
            "order_id": order_id,
            "sk": f"PAYMENT#{intent_id}",
            "provider_transaction_id": intent_id,
            "provider": "stripe",
            "status": status,
            "amount": amount,
            "currency": currency,
            "created_at": now,
            "updated_at": now,
        })


def get_db_client() -> DynamoDBClient:
    return DynamoDBClient()