"""
DynamoDB helper for the stripe_webhook Lambda.

Replaces the old psycopg2/RDS client. Auth is via the Lambda's execution
role (IAM) — no secrets/passwords needed.

Touches TWO tables:
  orders    (aws_dynamodb_table.orders)    — write (status update)
  payments  (aws_dynamodb_table.payments)  — read via ProviderTxnIndex + write

See payments_api/db.py's docstring for why the payments-table write is new
behavior versus the original RDS version (which never touched a payments
table at all).
"""

import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class DynamoDBClient:

    def __init__(self):
        self.orders_table_name = os.environ.get("ORDERS_TABLE_NAME")
        self.payments_table_name = os.environ.get("PAYMENTS_TABLE_NAME")

        missing = [
            name for name, val in (
                ("ORDERS_TABLE_NAME", self.orders_table_name),
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
        self.payments_table = resource.Table(self.payments_table_name)

    def update_order_status(self, order_id: str, status: str) -> None:
        self.orders_table.update_item(
            Key={"order_id": order_id, "sk": "ORDER"},
            UpdateExpression="SET #status = :status, updated_at = :now",
            ExpressionAttributeNames={"#status": "status"},  # 'status' is a reserved word
            ExpressionAttributeValues={":status": status, ":now": _now_iso()},
        )

    def find_payment_by_intent(self, intent_id: str) -> dict | None:
        """Look up a payment record via the sparse ProviderTxnIndex GSI."""
        resp = self.payments_table.query(
            IndexName="ProviderTxnIndex",
            KeyConditionExpression=Key("provider_transaction_id").eq(intent_id),
            Limit=1,
        )
        items = resp.get("Items", [])
        return items[0] if items else None

    def update_payment_status(self, order_id: str, sk: str, status: str, error_message: str | None = None) -> None:
        update_expr = "SET #status = :status, updated_at = :now"
        expr_values = {":status": status, ":now": _now_iso()}
        if error_message is not None:
            update_expr += ", error_message = :err"
            expr_values[":err"] = error_message

        self.payments_table.update_item(
            Key={"order_id": order_id, "sk": sk},
            UpdateExpression=update_expr,
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=expr_values,
        )


def get_db_client() -> DynamoDBClient:
    return DynamoDBClient()