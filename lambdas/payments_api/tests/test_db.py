from decimal import Decimal


class TestGetOrder:
    def test_returns_order(self, db, orders_table):
        orders_table.put_item(Item={"order_id": "o1", "sk": "ORDER", "status": "pending", "total_amount": "24.99", "user_id": "u1"})
        order = db.get_order("o1")
        assert order["status"] == "pending"
        assert order["user_id"] == "u1"

    def test_returns_none_for_deleted_order(self, db, orders_table):
        orders_table.put_item(Item={"order_id": "o1", "sk": "ORDER", "status": "pending", "deleted_at": "2024-01-01T00:00:00Z"})
        assert db.get_order("o1") is None

    def test_returns_none_for_missing_order(self, db, orders_table):
        assert db.get_order("does-not-exist") is None


class TestGetUserEmail:
    def test_returns_email(self, db, users_table):
        users_table.put_item(Item={"user_id": "u1", "email": "benny@example.com"})
        assert db.get_user_email("u1") == "benny@example.com"

    def test_returns_none_for_missing_user(self, db, users_table):
        assert db.get_user_email("does-not-exist") is None


class TestCreatePaymentRecord:
    def test_writes_expected_shape(self, db, payments_table):
        db.create_payment_record(
            order_id="o1", intent_id="pi_123", status="pending",
            amount="24.99", currency="cad",
        )
        item = payments_table.get_item(Key={"order_id": "o1", "sk": "PAYMENT#pi_123"})["Item"]
        assert item["provider_transaction_id"] == "pi_123"
        assert item["provider"] == "stripe"
        assert item["status"] == "pending"
        assert item["amount"] == "24.99"
        assert item["currency"] == "cad"

    def test_findable_via_provider_txn_index(self, db, payments_table):
        db.create_payment_record(order_id="o1", intent_id="pi_123", status="pending", amount="24.99", currency="cad")

        from boto3.dynamodb.conditions import Key
        resp = payments_table.query(
            IndexName="ProviderTxnIndex",
            KeyConditionExpression=Key("provider_transaction_id").eq("pi_123"),
        )
        items = resp["Items"]
        assert len(items) == 1
        assert items[0]["order_id"] == "o1"
