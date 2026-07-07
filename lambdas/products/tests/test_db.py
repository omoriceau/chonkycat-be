from boto3.dynamodb.conditions import Attr

import db as db_module


class TestBuildFilter:
    def test_no_filters(self):
        assert db_module._build_filter(active_only=False, include_deleted=True) is None

    def test_active_only(self):
        filt = db_module._build_filter(active_only=True, include_deleted=True)
        assert filt == Attr("active").eq(True)

    def test_exclude_deleted_only(self):
        filt = db_module._build_filter(active_only=False, include_deleted=False)
        assert filt == Attr("deleted_at").not_exists()

    def test_active_and_exclude_deleted_combined(self):
        filt = db_module._build_filter(active_only=True, include_deleted=False)
        expected = Attr("active").eq(True) & Attr("deleted_at").not_exists()
        assert filt == expected


class TestSoftDeleteAndRestore(object):
    def test_soft_delete_sets_deleted_at_and_updated_at(self, db, make_event):
        from handlers.create import handle_create_product
        from tests.conftest import body_of

        created = body_of(handle_create_product(db, make_event("POST", body={
            "sku": "DBX-1", "name": "X",
        })))["data"]

        updated = db.soft_delete_product(created["id"], "2026-01-01T00:00:00+00:00")
        assert updated["deleted_at"] == "2026-01-01T00:00:00+00:00"
        assert updated["updated_at"] == "2026-01-01T00:00:00+00:00"

    def test_restore_removes_deleted_at(self, db, make_event):
        from handlers.create import handle_create_product
        from tests.conftest import body_of

        created = body_of(handle_create_product(db, make_event("POST", body={
            "sku": "DBX-2", "name": "X",
        })))["data"]

        db.soft_delete_product(created["id"], "2026-01-01T00:00:00+00:00")
        restored = db.restore_product(created["id"], "2026-01-02T00:00:00+00:00")
        assert "deleted_at" not in restored
        assert restored["updated_at"] == "2026-01-02T00:00:00+00:00"

    def test_update_product_set_and_remove_together(self, db, make_event):
        from handlers.create import handle_create_product
        from tests.conftest import body_of

        created = body_of(handle_create_product(db, make_event("POST", body={
            "sku": "DBX-3", "name": "X", "category": "dry_food",
        })))["data"]

        updated = db.update_product(
            created["id"],
            updates={"name": "Renamed"},
            remove_attrs=["category"],
        )
        assert updated["name"] == "Renamed"
        assert "category" not in updated

    def test_hard_delete_actually_removes_item(self, db, make_event):
        from handlers.create import handle_create_product
        from tests.conftest import body_of

        created = body_of(handle_create_product(db, make_event("POST", body={
            "sku": "DBX-4", "name": "X",
        })))["data"]

        db.hard_delete_product(created["id"])
        assert db.get_product(created["id"]) is None

    def test_get_product_by_sku_finds_soft_deleted_items(self, db, make_event):
        """SKUs of soft-deleted products must still be treated as taken,
        so a deleted product's slot can't be silently reused."""
        from handlers.create import handle_create_product
        from handlers.delete import handle_delete_product
        from tests.conftest import body_of

        created = body_of(handle_create_product(db, make_event("POST", body={
            "sku": "DBX-5", "name": "X",
        })))["data"]
        handle_delete_product(db, created["id"])

        found = db.get_product_by_sku("DBX-5")
        assert found is not None
        assert found["product_id"] == created["id"]
