"""
Tests for db.py's DynamoDBClient against a moto-mocked users table.

Covers reads, the email-lock create/update/delete transactions, and their
failure paths (duplicate email, missing user).
"""

import pytest
from botocore.exceptions import ClientError


def _user_item(user_id="u1", email="shopper@example.com", **extra):
    item = {
        "user_id": user_id,
        "email": email,
        "first_name": "First",
        "last_name": "Last",
        "role": "customer",
        "status": "active",
        "created_at": "2024-01-01T00:00:00.000000Z",
        "updated_at": "2024-01-01T00:00:00.000000Z",
    }
    item.update(extra)
    return item


class TestGetUser:
    def test_returns_none_when_missing(self, db):
        assert db.get_user("nope") is None

    def test_returns_item_when_present(self, db):
        db.create_user(_user_item())
        item = db.get_user("u1")
        assert item["email"] == "shopper@example.com"

    def test_get_user_by_email(self, db):
        db.create_user(_user_item())
        item = db.get_user_by_email("shopper@example.com")
        assert item["user_id"] == "u1"

    def test_get_user_by_email_missing(self, db):
        assert db.get_user_by_email("nobody@example.com") is None

    def test_get_user_by_email_normalizes_case_and_whitespace(self, db):
        db.create_user(_user_item())
        assert db.get_user_by_email("  SHOPPER@EXAMPLE.COM  ") is not None


class TestListUsers:
    def test_lists_only_real_users_not_lock_items(self, db):
        db.create_user(_user_item(user_id="u1", email="a@example.com"))
        db.create_user(_user_item(user_id="u2", email="b@example.com"))
        items = db.list_users(role=None, status=None)
        assert {i["user_id"] for i in items} == {"u1", "u2"}

    def test_filters_by_role(self, db):
        db.create_user(_user_item(user_id="u1", email="a@example.com", role="admin"))
        db.create_user(_user_item(user_id="u2", email="b@example.com", role="customer"))
        items = db.list_users(role="admin", status=None)
        assert [i["user_id"] for i in items] == ["u1"]

    def test_filters_by_status(self, db):
        db.create_user(_user_item(user_id="u1", email="a@example.com", status="suspended"))
        db.create_user(_user_item(user_id="u2", email="b@example.com", status="active"))
        items = db.list_users(role=None, status="active")
        assert [i["user_id"] for i in items] == ["u2"]

    def test_empty_table(self, db):
        assert db.list_users(role=None, status=None) == []


class TestCreateUser:
    def test_creates_user_and_email_lock(self, db, users_table):
        db.create_user(_user_item())
        assert db.get_user("u1") is not None
        lock = users_table.get_item(Key={"user_id": "EMAIL#shopper@example.com"}).get("Item")
        assert lock is not None
        assert lock["linked_user_id"] == "u1"

    def test_duplicate_email_raises(self, db):
        import db as db_module

        db.create_user(_user_item(user_id="u1", email="shopper@example.com"))
        with pytest.raises(db_module.EmailAlreadyExists):
            db.create_user(_user_item(user_id="u2", email="shopper@example.com"))
        # The failed create must not have left u2 behind.
        assert db.get_user("u2") is None

    def test_duplicate_user_id_raises(self, db):
        """create_user doesn't distinguish which of the two transact items
        failed, so a colliding user_id (distinct email) still surfaces as
        EmailAlreadyExists rather than a generic ClientError."""
        import db as db_module

        db.create_user(_user_item(user_id="u1", email="a@example.com"))
        with pytest.raises(db_module.EmailAlreadyExists):
            db.create_user(_user_item(user_id="u1", email="different@example.com"))


class TestUpdateUser:
    def test_plain_update_without_email_change(self, db):
        db.create_user(_user_item())
        updated = db.update_user(
            "u1", {"first_name": "Changed"}, current_email="shopper@example.com", new_email=None,
        )
        assert updated["first_name"] == "Changed"
        assert updated["email"] == "shopper@example.com"

    def test_update_with_remove_keys(self, db):
        db.create_user(_user_item(phone="+1-555-0100"))
        updated = db.update_user(
            "u1", {}, current_email="shopper@example.com", new_email=None,
            remove_keys=["phone"],
        )
        assert "phone" not in updated

    def test_update_missing_user_raises_client_error(self, db):
        with pytest.raises(ClientError):
            db.update_user("nope", {"first_name": "X"}, current_email="a@example.com", new_email=None)

    def test_email_change_moves_the_lock(self, db, users_table):
        db.create_user(_user_item())
        updated = db.update_user(
            "u1", {}, current_email="shopper@example.com", new_email="new@example.com",
        )
        assert updated["email"] == "new@example.com"

        old_lock = users_table.get_item(Key={"user_id": "EMAIL#shopper@example.com"}).get("Item")
        new_lock = users_table.get_item(Key={"user_id": "EMAIL#new@example.com"}).get("Item")
        assert old_lock is None
        assert new_lock is not None
        assert new_lock["linked_user_id"] == "u1"

    def test_email_change_to_taken_email_raises_and_rolls_back(self, db):
        import db as db_module

        db.create_user(_user_item(user_id="u1", email="a@example.com"))
        db.create_user(_user_item(user_id="u2", email="b@example.com"))

        with pytest.raises(db_module.EmailAlreadyExists):
            db.update_user("u1", {}, current_email="a@example.com", new_email="b@example.com")

        # u1's own record and lock must be unaffected by the failed transaction.
        assert db.get_user("u1")["email"] == "a@example.com"
        assert db.get_user_by_email("a@example.com") is not None


class TestDeleteUser:
    def test_deletes_user_and_lock(self, db, users_table):
        db.create_user(_user_item())
        assert db.delete_user("u1", email="shopper@example.com") is True
        assert db.get_user("u1") is None
        lock = users_table.get_item(Key={"user_id": "EMAIL#shopper@example.com"}).get("Item")
        assert lock is None

    def test_delete_missing_user_returns_false(self, db):
        assert db.delete_user("nope", email="nope@example.com") is False
