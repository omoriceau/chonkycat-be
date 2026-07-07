from tests.conftest import body_of
from handlers.create import handle_create_product
from handlers.delete import handle_delete_product
from handlers.read import handle_get_product, handle_list_products


def _create(db, make_event, **overrides):
    body = {"sku": "SKU", "name": "Product"}
    body.update(overrides)
    resp = handle_create_product(db, make_event("POST", body=body))
    assert resp["statusCode"] == 201, resp
    return body_of(resp)["data"]


class TestGetSingleProduct:
    def test_get_existing_product(self, db, make_event):
        created = _create(db, make_event, sku="GET-1")
        resp = handle_get_product(db, created["id"])
        assert resp["statusCode"] == 200
        assert body_of(resp)["data"]["id"] == created["id"]

    def test_get_nonexistent_product(self, db):
        resp = handle_get_product(db, "does-not-exist")
        assert resp["statusCode"] == 404

    def test_get_blank_product_id(self, db):
        resp = handle_get_product(db, "")
        assert resp["statusCode"] == 400

    def test_soft_deleted_product_404s_by_default(self, db, make_event):
        created = _create(db, make_event, sku="GET-2")
        handle_delete_product(db, created["id"])
        resp = handle_get_product(db, created["id"])
        assert resp["statusCode"] == 404

    def test_soft_deleted_product_visible_with_include_deleted(self, db, make_event):
        created = _create(db, make_event, sku="GET-3")
        handle_delete_product(db, created["id"])
        resp = handle_get_product(db, created["id"], include_deleted=True)
        assert resp["statusCode"] == 200
        assert body_of(resp)["data"]["deleted_at"] is not None


class TestListProducts:
    def test_list_empty(self, db):
        resp = handle_list_products(db, {})
        assert resp["statusCode"] == 200
        payload = body_of(resp)
        assert payload["data"] == []
        assert payload["pagination"]["total_items"] == 0

    def test_list_includes_active_products_by_default(self, db, make_event):
        _create(db, make_event, sku="LIST-1")
        resp = handle_list_products(db, {})
        assert body_of(resp)["pagination"]["total_items"] == 1

    def test_list_excludes_inactive_by_default(self, db, make_event):
        _create(db, make_event, sku="LIST-2", active=False)
        resp = handle_list_products(db, {})
        assert body_of(resp)["pagination"]["total_items"] == 0

    def test_list_show_all_includes_inactive(self, db, make_event):
        _create(db, make_event, sku="LIST-3", active=False)
        resp = handle_list_products(db, {"show_all": "true"})
        assert body_of(resp)["pagination"]["total_items"] == 1

    def test_list_excludes_soft_deleted_by_default(self, db, make_event):
        created = _create(db, make_event, sku="LIST-4")
        handle_delete_product(db, created["id"])
        resp = handle_list_products(db, {})
        assert body_of(resp)["pagination"]["total_items"] == 0

    def test_list_show_deleted_includes_soft_deleted(self, db, make_event):
        created = _create(db, make_event, sku="LIST-5")
        handle_delete_product(db, created["id"])
        resp = handle_list_products(db, {"show_deleted": "true"})
        assert body_of(resp)["pagination"]["total_items"] == 1

    def test_list_filters_by_category(self, db, make_event):
        _create(db, make_event, sku="LIST-6", category="wet_food")
        _create(db, make_event, sku="LIST-7", category="dry_food")
        resp = handle_list_products(db, {"category": "wet_food"})
        payload = body_of(resp)
        assert payload["pagination"]["total_items"] == 1
        assert payload["data"][0]["category"] == "wet_food"

    def test_list_filters_by_low_stock(self, db, make_event):
        _create(db, make_event, sku="LIST-8", current_stock=1, low_stock_threshold=10)
        _create(db, make_event, sku="LIST-9", current_stock=100, low_stock_threshold=10)
        resp = handle_list_products(db, {"low_stock": "true"})
        payload = body_of(resp)
        assert payload["pagination"]["total_items"] == 1
        assert payload["data"][0]["sku"] == "LIST-8"

    def test_list_low_stock_combined_with_category(self, db, make_event):
        _create(db, make_event, sku="LIST-10", category="snacks", current_stock=1, low_stock_threshold=10)
        _create(db, make_event, sku="LIST-11", category="snacks", current_stock=100, low_stock_threshold=10)
        _create(db, make_event, sku="LIST-12", category="dry_food", current_stock=1, low_stock_threshold=10)
        resp = handle_list_products(db, {"category": "snacks", "low_stock": "true"})
        payload = body_of(resp)
        assert payload["pagination"]["total_items"] == 1
        assert payload["data"][0]["sku"] == "LIST-10"

    def test_list_pagination(self, db, make_event):
        for i in range(5):
            _create(db, make_event, sku=f"PAGE-{i}", name=f"Product {i}")
        resp = handle_list_products(db, {"page": "1", "page_size": "2"})
        payload = body_of(resp)
        assert len(payload["data"]) == 2
        assert payload["pagination"]["total_items"] == 5
        assert payload["pagination"]["total_pages"] == 3
        assert payload["pagination"]["has_next"] is True
        assert payload["pagination"]["has_prev"] is False

        resp = handle_list_products(db, {"page": "3", "page_size": "2"})
        payload = body_of(resp)
        assert len(payload["data"]) == 1
        assert payload["pagination"]["has_next"] is False
        assert payload["pagination"]["has_prev"] is True

    def test_list_sorted_by_category_then_name(self, db, make_event):
        _create(db, make_event, sku="SORT-1", name="Zebra", category="dry_food")
        _create(db, make_event, sku="SORT-2", name="Apple", category="dry_food")
        resp = handle_list_products(db, {"category": "dry_food"})
        names = [p["name"] for p in body_of(resp)["data"]]
        assert names == ["Apple", "Zebra"]
