"""
Direct unit tests for db.py's DynamoDBClient — the branches not already
exercised indirectly through service-layer tests (env var validation,
transaction-cancellation logging, error-branch handling on writes, and a
couple of plain read helpers).
"""
import os
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

import db as db_module
from db import DynamoDBClient, InsufficientStock, _log_transaction_cancellation


class TestInsufficientStock:
    def test_carries_product_id_and_message(self):
        exc = InsufficientStock("p1")
        assert exc.product_id == "p1"
        assert "p1" in str(exc)


class TestLogTransactionCancellation:
    def test_logs_cancellation_reasons_when_present(self, caplog):
        e = ClientError(
            {"Error": {"Code": "TransactionCanceledException", "Message": "x"},
             "CancellationReasons": [{"Code": "ConditionalCheckFailed"}]},
            "TransactWriteItems",
        )
        with caplog.at_level("ERROR"):
            _log_transaction_cancellation(e, "test context")
        assert any("CancellationReasons" in r.message for r in caplog.records)

    def test_logs_plain_message_when_no_reasons(self, caplog):
        e = ClientError({"Error": {"Code": "Boom", "Message": "x"}}, "TransactWriteItems")
        with caplog.at_level("ERROR"):
            _log_transaction_cancellation(e, "test context")
        assert any("test context" in r.message for r in caplog.records)
        assert not any("CancellationReasons" in r.message for r in caplog.records)


class TestMissingEnvVars:
    def test_raises_runtime_error_when_table_names_missing(self, monkeypatch):
        monkeypatch.delenv("ORDERS_TABLE_NAME", raising=False)
        monkeypatch.delenv("PRODUCTS_TABLE_NAME", raising=False)
        monkeypatch.delenv("PROMOTIONS_TABLE_NAME", raising=False)

        with pytest.raises(RuntimeError, match="ORDERS_TABLE_NAME"):
            DynamoDBClient()


class TestCreateOrderTransaction:
    def test_duplicate_order_id_raises_and_logs_cancellation(self, db, orders_table, caplog):
        order_item = {
            "order_id": "dup-1", "sk": "ORDER", "user_id": "u1", "status": "pending",
            "created_at": "2026-01-01T00:00:00.000000Z",
        }
        db.create_order_transaction(order_item, [], [])

        with caplog.at_level("ERROR"):
            with pytest.raises(ClientError, match="TransactionCanceledException"):
                db.create_order_transaction(order_item, [], [])
        assert any("create_order_transaction failed" in r.message for r in caplog.records)


class TestDecrementStock:
    def test_insufficient_stock_logs_warning_and_does_not_raise(self, db, products_table, make_product):
        make_product(products_table, product_id="p1", qty=1)
        db.decrement_stock([{"product_id": "p1", "quantity": 5}])
        remaining = products_table.get_item(Key={"product_id": "p1"})["Item"]
        assert int(remaining["qty"]) == 1  # unchanged — condition failed

    def test_generic_client_error_is_logged_and_does_not_raise(self, db, monkeypatch):
        fake_table = MagicMock()
        fake_table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "bad request"}}, "UpdateItem"
        )
        monkeypatch.setattr(db, "products_table", fake_table)
        db.decrement_stock([{"product_id": "p1", "quantity": 1}])  # must not raise


class TestSoftDeleteOrder:
    def test_returns_false_for_missing_order(self, db):
        assert db.soft_delete_order("nonexistent") is False

    def test_returns_false_when_already_deleted(self, db, orders_table):
        orders_table.put_item(Item={
            "order_id": "o1", "sk": "ORDER", "deleted_at": "2026-01-01T00:00:00.000000Z",
        })
        assert db.soft_delete_order("o1") is False

    def test_returns_true_on_success(self, db, orders_table):
        orders_table.put_item(Item={"order_id": "o1", "sk": "ORDER"})
        assert db.soft_delete_order("o1") is True
        stored = orders_table.get_item(Key={"order_id": "o1", "sk": "ORDER"})["Item"]
        assert "deleted_at" in stored

    def test_reraises_non_conditional_client_error(self, db, monkeypatch):
        fake_table = MagicMock()
        fake_table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}}, "UpdateItem"
        )
        monkeypatch.setattr(db, "orders_table", fake_table)
        with pytest.raises(ClientError):
            db.soft_delete_order("o1")


class TestUpdateOrderTransaction:
    def test_missing_order_raises_and_logs(self, db, caplog):
        with caplog.at_level("ERROR"):
            with pytest.raises(ClientError, match="TransactionCanceledException"):
                db.update_order_transaction(
                    order_id="missing-order",
                    order_updates={"customer_notes": "x"},
                    old_item_sks=[],
                    new_item_children=None,
                )
        assert any("update_order_transaction failed" in r.message for r in caplog.records)

    def test_replaces_item_children_when_provided(self, db, orders_table):
        orders_table.put_item(Item={"order_id": "o1", "sk": "ORDER", "status": "pending"})
        orders_table.put_item(Item={"order_id": "o1", "sk": "ITEM#0000", "product_id": "old"})

        db.update_order_transaction(
            order_id="o1",
            order_updates={"subtotal": "10.00"},
            old_item_sks=["ITEM#0000"],
            new_item_children=[{"order_id": "o1", "sk": "ITEM#0000", "product_id": "new"}],
        )

        result = db.get_order_with_children("o1")
        assert len(result["items"]) == 1
        assert result["items"][0]["product_id"] == "new"
        assert result["order"]["subtotal"] == "10.00"


class TestBatchGetProducts:
    def test_empty_product_ids_returns_empty_dict(self, db):
        assert db.batch_get_products([]) == {}

    def test_returns_only_existing_products(self, db, products_table, make_product):
        make_product(products_table, product_id="p1")
        result = db.batch_get_products(["p1", "does-not-exist"])
        assert set(result.keys()) == {"p1"}


class TestUpdateProductReorderState:
    def test_sets_flag_when_at_or_below_threshold(self, db, products_table, make_product):
        make_product(products_table, product_id="p1", qty=2, low_stock_threshold=5)
        db.update_product_reorder_state("p1", current_qty=2, threshold=5)
        product = products_table.get_item(Key={"product_id": "p1"})["Item"]
        assert product["reorder_flag"] == "true"

    def test_removes_flag_when_restocked_above_threshold(self, db, products_table, make_product):
        make_product(products_table, product_id="p1", qty=2, low_stock_threshold=5)
        db.update_product_reorder_state("p1", current_qty=2, threshold=5)
        db.update_product_reorder_state("p1", current_qty=50, threshold=5)
        product = products_table.get_item(Key={"product_id": "p1"})["Item"]
        assert "reorder_flag" not in product


class TestGetPromotion:
    def test_returns_none_when_not_found(self, db, promotions_table):
        assert db.get_promotion("NOPE") is None

    def test_returns_item_when_found(self, db, promotions_table, make_promotion):
        make_promotion(promotions_table, code="SAVE10")
        promo = db.get_promotion("SAVE10")
        assert promo["code"] == "SAVE10"


class TestGetDbClient:
    def test_returns_a_dynamodb_client_instance(self):
        client = db_module.get_db_client()
        assert isinstance(client, DynamoDBClient)
