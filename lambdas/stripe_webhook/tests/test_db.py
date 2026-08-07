from boto3.dynamodb.conditions import Key


class TestUpdateOrderStatus:
    def test_updates_status(self, db, orders_table):
        orders_table.put_item(Item={"order_id": "o1", "sk": "ORDER", "status": "pending"})
        db.update_order_status("o1", "completed")
        item = orders_table.get_item(Key={"order_id": "o1", "sk": "ORDER"})["Item"]
        assert item["status"] == "completed"
        assert "updated_at" in item


class TestGetOrderWithChildren:
    def test_returns_order_and_items(self, db, orders_table):
        orders_table.put_item(Item={"order_id": "o1", "sk": "ORDER", "status": "pending", "customer_email": "a@b.com"})
        orders_table.put_item(Item={"order_id": "o1", "sk": "ITEM#0001", "name_snapshot": "Salmon Crisps", "quantity": 2})
        orders_table.put_item(Item={"order_id": "o1", "sk": "ITEM#0000", "name_snapshot": "Tuna Tube", "quantity": 1})

        result = db.get_order_with_children("o1")

        assert result["order"]["order_id"] == "o1"
        assert result["order"]["customer_email"] == "a@b.com"
        # Sorted by sk, so ITEM#0000 (Tuna Tube) comes before ITEM#0001 (Salmon Crisps)
        assert [i["name_snapshot"] for i in result["items"]] == ["Tuna Tube", "Salmon Crisps"]

    def test_returns_none_when_order_not_found(self, db, orders_table):
        assert db.get_order_with_children("does-not-exist") is None

    def test_does_not_include_other_orders_items(self, db, orders_table):
        orders_table.put_item(Item={"order_id": "o1", "sk": "ORDER", "status": "pending"})
        orders_table.put_item(Item={"order_id": "o1", "sk": "ITEM#0000", "name_snapshot": "Tuna Tube"})
        orders_table.put_item(Item={"order_id": "o2", "sk": "ORDER", "status": "pending"})
        orders_table.put_item(Item={"order_id": "o2", "sk": "ITEM#0000", "name_snapshot": "Unrelated"})

        result = db.get_order_with_children("o1")

        assert len(result["items"]) == 1
        assert result["items"][0]["name_snapshot"] == "Tuna Tube"


class TestFindPaymentByIntent:
    def test_finds_via_provider_txn_index(self, db, payments_table):
        payments_table.put_item(Item={
            "order_id": "o1", "sk": "PAYMENT#pi_123",
            "provider_transaction_id": "pi_123", "status": "pending",
        })
        payment = db.find_payment_by_intent("pi_123")
        assert payment["order_id"] == "o1"
        assert payment["sk"] == "PAYMENT#pi_123"

    def test_returns_none_when_not_found(self, db, payments_table):
        assert db.find_payment_by_intent("does-not-exist") is None


class TestUpdatePaymentStatus:
    def test_updates_status(self, db, payments_table):
        payments_table.put_item(Item={
            "order_id": "o1", "sk": "PAYMENT#pi_123",
            "provider_transaction_id": "pi_123", "status": "pending",
        })
        db.update_payment_status("o1", "PAYMENT#pi_123", "succeeded")
        item = payments_table.get_item(Key={"order_id": "o1", "sk": "PAYMENT#pi_123"})["Item"]
        assert item["status"] == "succeeded"

    def test_records_error_message_when_given(self, db, payments_table):
        payments_table.put_item(Item={
            "order_id": "o1", "sk": "PAYMENT#pi_123",
            "provider_transaction_id": "pi_123", "status": "pending",
        })
        db.update_payment_status("o1", "PAYMENT#pi_123", "failed", error_message="card declined")
        item = payments_table.get_item(Key={"order_id": "o1", "sk": "PAYMENT#pi_123"})["Item"]
        assert item["status"] == "failed"
        assert item["error_message"] == "card declined"
