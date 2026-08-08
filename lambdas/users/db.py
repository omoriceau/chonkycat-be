"""
DynamoDB helper for the users Lambda.

Replaces the old psycopg2/RDS client entirely — auth is via the Lambda's
execution role (IAM), so there's no password/secrets handling left at all.

Table: aws_dynamodb_table.users (see terraform)
  hash_key: user_id (S)
  GSI EmailIndex: hash=email

EMAIL UNIQUENESS
-----------------
Postgres enforced `email UNIQUE` at the DB level. DynamoDB has no cross-item
uniqueness constraint, and the EmailIndex GSI is eventually consistent, so a
plain "query by email, then put if not found" check has a race window under
concurrent signups.

To get the same atomic guarantee Postgres gave us for free, every real user
item is paired with a hidden "lock" item in the *same* table:

    user_id = "EMAIL#<lowercased email>"   (no `email` attribute set)

Because the lock item has no `email` attribute, it never shows up in
EmailIndex — only real user items do. Claiming an email is a conditional
Put on that lock item's user_id (attribute_not_exists), wrapped in the same
transaction as the real write, so either both succeed or neither does.
"""

import logging
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

logger = logging.getLogger()

EMAIL_LOCK_PREFIX = "EMAIL#"


def _email_lock_key(email: str) -> str:
    return f"{EMAIL_LOCK_PREFIX}{email.strip().lower()}"


def now_iso() -> str:
    """ISO-8601 UTC timestamp that also sorts correctly as a plain string —
    used instead of the old serial `id` for "list in creation order"."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class EmailAlreadyExists(Exception):
    def __init__(self, email: str):
        self.email = email
        super().__init__(f"A user with email '{email}' already exists")


class DynamoDBClient:
    """Thin wrapper around a boto3 Table resource for the users table."""

    def __init__(self):
        table_name = os.environ.get("USERS_TABLE_NAME")
        if not table_name:
            raise RuntimeError(
                "Missing required environment variable: USERS_TABLE_NAME. "
                "Set this to the users table name output by terraform "
                "(aws_dynamodb_table.users.name)."
            )
        region = os.environ.get("AWS_REGION")
        self._resource = boto3.resource("dynamodb", region_name=region)
        # A plain client, NOT self._resource.meta.client: the resource
        # registers a before-parameter-build hook on its shared client that
        # auto-serializes any 'AttributeValue'-shaped field on every call
        # made through it, resource-level or not. create_user/update_user/
        # delete_user below pre-serialize items themselves (via _to_dynamo)
        # for transact_write_items, which the low-level API requires in raw
        # {"S": ...} form — reusing the resource's client would run that
        # hook too and double-serialize every value (e.g. user_id: {"S":
        # "x"} -> {"M": {"S": {"S": "x"}}}), which DynamoDB then rejects as
        # a key-type mismatch (surfaces as a spurious TransactionCanceled /
        # EmailAlreadyExists, even on an empty table).
        self._client = boto3.client("dynamodb", region_name=region)
        self.table_name = table_name
        self.table = self._resource.Table(table_name)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_user(self, user_id: str) -> dict | None:
        resp = self.table.get_item(Key={"user_id": user_id})
        return resp.get("Item")

    def get_user_by_email(self, email: str) -> dict | None:
        """Eventually-consistent convenience lookup via the GSI."""
        resp = self.table.query(
            IndexName="EmailIndex",
            KeyConditionExpression=Key("email").eq(email.strip().lower()),
            Limit=1,
        )
        items = resp.get("Items", [])
        return items[0] if items else None

    def list_users(self, role: str | None, status: str | None) -> list[dict]:
        """
        Scan the table for real user items (lock items are filtered out by
        requiring `email` to exist — lock items never have that attribute).
        No GSI covers role/status filtering, so this is a Scan either way.
        """
        filter_expr = Attr("email").exists()
        if role:
            filter_expr = filter_expr & Attr("role").eq(role)
        if status:
            filter_expr = filter_expr & Attr("status").eq(status)

        items = []
        kwargs = {"FilterExpression": filter_expr}
        while True:
            resp = self.table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create_user(self, user_item: dict) -> None:
        """
        Atomically create the user item + claim its email lock item.
        Raises EmailAlreadyExists if the email is already taken.
        """
        lock_item = {"user_id": _email_lock_key(user_item["email"]), "linked_user_id": user_item["user_id"]}

        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": _to_dynamo(user_item),
                            "ConditionExpression": "attribute_not_exists(user_id)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": _to_dynamo(lock_item),
                            "ConditionExpression": "attribute_not_exists(user_id)",
                        }
                    },
                ]
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "TransactionCanceledException":
                raise EmailAlreadyExists(user_item["email"]) from e
            raise

    @staticmethod
    def _build_update_expression(updates: dict, remove_keys: list[str]) -> tuple[str, dict, dict]:
        """Builds a combined `SET ... REMOVE ...` expression — used both for
        the plain-update path and the email-changing transaction below."""
        set_parts = []
        expr_names = {}
        expr_values = {}
        for i, (k, v) in enumerate(updates.items()):
            placeholder = f"#f{i}"
            value_ph = f":v{i}"
            set_parts.append(f"{placeholder} = {value_ph}")
            expr_names[placeholder] = k
            expr_values[value_ph] = v

        remove_parts = []
        for i, k in enumerate(remove_keys):
            placeholder = f"#r{i}"
            remove_parts.append(placeholder)
            expr_names[placeholder] = k

        expr = "SET " + ", ".join(set_parts)
        if remove_parts:
            expr += " REMOVE " + ", ".join(remove_parts)

        return expr, expr_names, expr_values

    def update_user(
        self,
        user_id: str,
        updates: dict,
        current_email: str,
        new_email: str | None,
        remove_keys: list[str] | None = None,
    ) -> dict:
        """
        Apply a partial update. If the email is changing, this is done as a
        transaction that releases the old email lock and claims the new one
        alongside the attribute update, so a duplicate-email race is still
        caught atomically. If the email isn't changing, it's a plain
        UpdateItem. `remove_keys` (e.g. ["address"]) is applied as a REMOVE
        clause alongside the SET, for attributes being cleared rather than
        set to a new value — DynamoDB can't store None the way SET expects.
        """
        updates = dict(updates)
        updates["updated_at"] = now_iso()
        remove_keys = remove_keys or []

        if new_email is None or new_email == current_email:
            update_expr, expr_names, expr_values = self._build_update_expression(updates, remove_keys)

            resp = self.table.update_item(
                Key={"user_id": user_id},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
                ConditionExpression="attribute_exists(user_id)",
                ReturnValues="ALL_NEW",
            )
            return resp["Attributes"]

        # Email is changing — move the lock transactionally.
        old_lock_key = _email_lock_key(current_email)
        new_lock_key = _email_lock_key(new_email)
        updates["email"] = new_email

        update_expr, expr_names, expr_values = self._build_update_expression(updates, remove_keys)

        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self.table_name,
                            "Key": _to_dynamo({"user_id": user_id}),
                            "UpdateExpression": update_expr,
                            "ExpressionAttributeNames": expr_names,
                            "ExpressionAttributeValues": _to_dynamo(expr_values),
                            "ConditionExpression": "attribute_exists(user_id)",
                        }
                    },
                    {
                        "Delete": {
                            "TableName": self.table_name,
                            "Key": _to_dynamo({"user_id": old_lock_key}),
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": _to_dynamo({"user_id": new_lock_key, "linked_user_id": user_id}),
                            "ConditionExpression": "attribute_not_exists(user_id)",
                        }
                    },
                ]
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "TransactionCanceledException":
                raise EmailAlreadyExists(new_email) from e
            raise

        return self.get_user(user_id)

    def delete_user(self, user_id: str, email: str) -> bool:
        """Delete the user item and release its email lock together."""
        transact_items = [
            {
                "Delete": {
                    "TableName": self.table_name,
                    "Key": _to_dynamo({"user_id": user_id}),
                    "ConditionExpression": "attribute_exists(user_id)",
                }
            },
            {
                "Delete": {
                    "TableName": self.table_name,
                    "Key": _to_dynamo({"user_id": _email_lock_key(email)}),
                }
            },
        ]
        logger.info("delete_user transact_items=%r", transact_items)
        try:
            self._client.transact_write_items(TransactItems=transact_items)
            return True
        except ClientError as e:
            logger.exception("delete_user ClientError | user_id=%s response=%r", user_id, e.response)
            if e.response["Error"]["Code"] == "TransactionCanceledException":
                logger.exception(
                    "delete_user transaction cancelled | user_id=%s reasons=%s",
                    user_id, e.response.get("CancellationReasons"),
                )
                return False
            raise


# ---------------------------------------------------------------------------
# Plain-dict <-> DynamoDB attribute-value conversion for the low-level
# transact_write_items client (the resource-level Table object handles this
# for us automatically, but the low-level client used for transactions does
# not).
# ---------------------------------------------------------------------------

from boto3.dynamodb.types import TypeSerializer

_serializer = TypeSerializer()


def _to_dynamo(item: dict) -> dict:
    return {k: _serializer.serialize(v) for k, v in item.items()}


def get_db_client() -> DynamoDBClient:
    return DynamoDBClient()
