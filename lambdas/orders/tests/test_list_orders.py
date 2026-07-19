from tests.conftest import body_of


def _seed_order(orders_table, order_id, status="pending", total="42.00", user_id="user-1",
                 created_at="2026-01-01T00:00:00.000000Z", item_count=2, deleted_at=None):
    order = {
        "order_id": order_id,
        "sk": "ORDER",
        "user_id": user_id,
        "status": status,
        "subtotal": total,
        "tax_amount": "0.00",
        "shipping_amount": "0.00",
        "total_amount": total,
        "created_at": created_at,
        "updated_at": created_at,
        # Fields that must NOT leak into the list response.
        "customer_email": "shopper@example.com",
        "customer_notes": "Leave at door",
        "shipping_name": "Benny Garcia",
        "shipping_address1": "42 Maple Ave",
        "shipping_city": "Toronto",
        "shipping_province": "ON",
        "shipping_postal_code": "M5V 2H1",
        "shipping_country": "Canada",
    }
    if deleted_at:
        order["deleted_at"] = deleted_at
    orders_table.put_item(Item=order)

    for i in range(item_count):
        orders_table.put_item(Item={
            "order_id": order_id,
            "sk": f"ITEM#{i:04d}",
            "product_id": f"prod-{i}",
            "quantity": 1,
            "unit_price": "10.00",
            "line_total": "10.00",
            "name_snapshot": f"Product {i}",
        })
    return order_id


class TestListOrdersService:
    def test_returns_minimal_fields_only(self, service, orders_table):
        _seed_order(orders_table, "o1", item_count=3, total="99.50")

        result = service.list_orders()

        assert result["pagination"]["total_items"] == 1
        summary = result["data"][0]
        assert summary == {
            "order_id": "o1",
            "user_id": "user-1",
            "status": "pending",
            "item_count": 3,
            "total": "99.50",
            "created_at": "2026-01-01T00:00:00.000000Z",
        }
        # Nothing private leaked through.
        assert "customer_email" not in summary
        assert "customer_notes" not in summary
        assert "shipping_name" not in summary
        assert "shipping_address1" not in summary

    def test_excludes_carts_by_default(self, service, orders_table):
        _seed_order(orders_table, "o-cart", status="cart")
        _seed_order(orders_table, "o-pending", status="pending")

        result = service.list_orders()

        ids = [o["order_id"] for o in result["data"]]
        assert ids == ["o-pending"]

    def test_include_carts_flag_includes_them(self, service, orders_table):
        _seed_order(orders_table, "o-cart", status="cart")
        _seed_order(orders_table, "o-pending", status="pending")

        result = service.list_orders(include_carts=True)

        ids = {o["order_id"] for o in result["data"]}
        assert ids == {"o-cart", "o-pending"}

    def test_status_filter(self, service, orders_table):
        _seed_order(orders_table, "o1", status="pending")
        _seed_order(orders_table, "o2", status="completed")

        result = service.list_orders(status="completed")

        assert [o["order_id"] for o in result["data"]] == ["o2"]

    def test_status_filter_for_cart_bypasses_the_default_exclusion(self, service, orders_table):
        _seed_order(orders_table, "o-cart", status="cart")
        _seed_order(orders_table, "o-pending", status="pending")

        result = service.list_orders(status="cart")

        assert [o["order_id"] for o in result["data"]] == ["o-cart"]

    def test_excludes_deleted_by_default(self, service, orders_table):
        _seed_order(orders_table, "o1", deleted_at="2026-02-01T00:00:00.000000Z")
        _seed_order(orders_table, "o2")

        result = service.list_orders()

        assert [o["order_id"] for o in result["data"]] == ["o2"]

    def test_include_deleted_flag(self, service, orders_table):
        _seed_order(orders_table, "o1", deleted_at="2026-02-01T00:00:00.000000Z")
        _seed_order(orders_table, "o2")

        result = service.list_orders(include_deleted=True)

        ids = {o["order_id"] for o in result["data"]}
        assert ids == {"o1", "o2"}

    def test_sorted_newest_first(self, service, orders_table):
        _seed_order(orders_table, "old", created_at="2026-01-01T00:00:00.000000Z")
        _seed_order(orders_table, "new", created_at="2026-06-01T00:00:00.000000Z")

        result = service.list_orders()

        assert [o["order_id"] for o in result["data"]] == ["new", "old"]

    def test_pagination(self, service, orders_table):
        for i in range(5):
            _seed_order(orders_table, f"o{i}", created_at=f"2026-01-0{i + 1}T00:00:00.000000Z")

        page1 = service.list_orders(page=1, page_size=2)
        assert len(page1["data"]) == 2
        assert page1["pagination"]["total_items"] == 5
        assert page1["pagination"]["total_pages"] == 3
        assert page1["pagination"]["has_next"] is True
        assert page1["pagination"]["has_prev"] is False

        page3 = service.list_orders(page=3, page_size=2)
        assert len(page3["data"]) == 1
        assert page3["pagination"]["has_next"] is False
        assert page3["pagination"]["has_prev"] is True


class TestScanAllOrdersPagination:
    def test_stitches_together_multiple_100_row_scan_pages(self, db, orders_table):
        """55 orders x (1 ORDER + 1 ITEM#) = 110 rows, past db.py's
        SCAN_PAGE_SIZE=100 Limit — forces scan_all_orders to follow
        LastEvaluatedKey across at least two internal Scan calls."""
        for i in range(55):
            _seed_order(orders_table, f"o{i}", item_count=1, created_at=f"2026-01-01T00:00:{i:02d}.000000Z")

        rows = db.scan_all_orders()

        assert len(rows) == 110
        order_rows = [r for r in rows if r["sk"] == "ORDER"]
        item_rows = [r for r in rows if r["sk"].startswith("ITEM#")]
        assert len(order_rows) == 55
        assert len(item_rows) == 55
        assert {r["order_id"] for r in order_rows} == {f"o{i}" for i in range(55)}


class TestListOrdersRouting:
    def test_get_orders_no_path_id_routes_to_list(self, orders_table, make_event):
        import lambda_handler

        _seed_order(orders_table, "o1")

        resp = lambda_handler.lambda_handler(
            make_event("GET", resource="/orders", path_params=None), None
        )
        assert resp["statusCode"] == 200
        body = body_of(resp)
        assert "pagination" in body
        assert body["data"][0]["order_id"] == "o1"

    def test_get_orders_with_path_id_routes_to_get_single(self, orders_table, make_event):
        import lambda_handler

        _seed_order(orders_table, "o1")

        resp = lambda_handler.lambda_handler(
            make_event("GET", resource="/orders/{orderId}", path_params={"orderId": "o1"}), None
        )
        assert resp["statusCode"] == 200
        assert body_of(resp)["order"]["order_id"] == "o1"

    def test_get_orders_missing_order_still_404s(self, orders_table, make_event):
        import lambda_handler

        resp = lambda_handler.lambda_handler(
            make_event("GET", resource="/orders/{orderId}", path_params={"orderId": "nope"}), None
        )
        assert resp["statusCode"] == 404

    def test_query_params_reach_the_service(self, orders_table, make_event):
        import lambda_handler

        _seed_order(orders_table, "o-cart", status="cart")
        _seed_order(orders_table, "o-pending", status="pending")

        resp = lambda_handler.lambda_handler(
            make_event("GET", resource="/orders", path_params=None, qs={"status": "cart"}), None
        )
        assert resp["statusCode"] == 200
        assert [o["order_id"] for o in body_of(resp)["data"]] == ["o-cart"]
