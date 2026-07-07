from tests.conftest import body_of
from handlers.create import handle_create_product
from handlers.delete import handle_delete_product
from handlers.read import handle_get_product


def _create(db, make_event, **overrides):
    body = {"sku": "SKU", "name": "Product"}
    body.update(overrides)
    resp = handle_create_product(db, make_event("POST", body=body))
    assert resp["statusCode"] == 201, resp
    return body_of(resp)["data"]


class TestDeleteProduct:
    def test_delete_existing_product(self, db, make_event):
        created = _create(db, make_event, sku="DEL-1")
        resp = handle_delete_product(db, created["id"])
        assert resp["statusCode"] == 204
        assert resp["body"] == ""

    def test_delete_is_soft_not_hard(self, db, make_event):
        """The item must remain in the table with deleted_at set — this
        is what lets historical orders keep referencing the product_id."""
        created = _create(db, make_event, sku="DEL-2")
        handle_delete_product(db, created["id"])
        raw = db.get_product(created["id"])
        assert raw is not None
        assert raw.get("deleted_at")

    def test_delete_nonexistent_product(self, db):
        resp = handle_delete_product(db, "does-not-exist")
        assert resp["statusCode"] == 404

    def test_delete_already_deleted_product(self, db, make_event):
        created = _create(db, make_event, sku="DEL-3")
        handle_delete_product(db, created["id"])
        resp = handle_delete_product(db, created["id"])
        assert resp["statusCode"] == 404

    def test_delete_blank_product_id(self, db):
        resp = handle_delete_product(db, "")
        assert resp["statusCode"] == 400

    def test_deleted_product_not_visible_via_get(self, db, make_event):
        created = _create(db, make_event, sku="DEL-4")
        handle_delete_product(db, created["id"])
        resp = handle_get_product(db, created["id"])
        assert resp["statusCode"] == 404

    def test_delete_preserves_other_fields(self, db, make_event):
        created = _create(db, make_event, sku="DEL-5", name="Keep Me", price=9.99)
        handle_delete_product(db, created["id"])
        raw = db.get_product(created["id"])
        assert raw["name"] == "Keep Me"
        assert raw["sku"] == "DEL-5"
