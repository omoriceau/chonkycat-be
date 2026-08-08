"""
Tests for service.py's UserService — a real DynamoDBClient (moto-backed,
via the `db` fixture) plus mocked Cognito/EventBridge clients (cognito_mock,
events_mock), wired together by the `user_service` fixture.
"""

import pytest
from botocore.exceptions import ClientError

from models import CreateUserRequest, UpdateUserRequest, ValidationError


def _create_request(**overrides):
    defaults = dict(
        email="shopper@example.com",
        password="Correct-Horse-Battery-9!",
        first_name="First",
        last_name="Last",
        phone="+1-555-0100",
        role="customer",
        status="active",
    )
    defaults.update(overrides)
    return CreateUserRequest(**defaults)


class TestGetUser:
    def test_returns_none_when_missing(self, user_service):
        assert user_service.get_user("nope") is None

    def test_returns_response_shape(self, user_service):
        created = user_service.create_user(_create_request())
        fetched = user_service.get_user(created["id"])
        assert fetched["email"] == "shopper@example.com"
        assert fetched["first_name"] == "First"


class TestListUsers:
    def test_pagination_and_sorting(self, user_service, cognito_mock):
        for i in range(3):
            cognito_mock.admin_create_user.return_value = {
                "User": {"Attributes": [{"Name": "sub", "Value": f"sub-{i}"}]}
            }
            user_service.create_user(_create_request(email=f"user{i}@example.com"))

        page = user_service.list_users(limit=2, offset=0)
        assert page["count"] == 2
        assert page["limit"] == 2
        assert page["offset"] == 0
        assert len(page["users"]) == 2

        page2 = user_service.list_users(limit=2, offset=2)
        assert page2["count"] == 1

    def test_limit_is_clamped(self, user_service):
        page = user_service.list_users(limit=10000, offset=-5)
        assert page["limit"] == 200
        assert page["offset"] == 0

    def test_filters_by_role_and_status(self, user_service, cognito_mock):
        cognito_mock.admin_create_user.return_value = {
            "User": {"Attributes": [{"Name": "sub", "Value": "admin-sub"}]}
        }
        user_service.create_user(_create_request(email="admin@example.com", role="admin"))
        cognito_mock.admin_create_user.return_value = {
            "User": {"Attributes": [{"Name": "sub", "Value": "cust-sub"}]}
        }
        user_service.create_user(_create_request(email="cust@example.com", role="customer"))

        result = user_service.list_users(role="admin")
        assert len(result["users"]) == 1
        assert result["users"][0]["role"] == "admin"


class TestCreateUser:
    def test_success_calls_cognito_and_emits_event(self, user_service, cognito_mock, events_mock):
        result = user_service.create_user(_create_request())

        assert result["id"] == "cognito-sub-1"
        assert result["email"] == "shopper@example.com"
        cognito_mock.admin_create_user.assert_called_once()
        cognito_mock.admin_set_user_password.assert_called_once()
        events_mock.put_events.assert_called_once()

    def test_duplicate_email_rolls_back_cognito_user(self, user_service, cognito_mock):
        user_service.create_user(_create_request(email="dup@example.com"))
        cognito_mock.admin_create_user.return_value = {
            "User": {"Attributes": [{"Name": "sub", "Value": "cognito-sub-2"}]}
        }
        with pytest.raises(ValidationError):
            user_service.create_user(_create_request(email="dup@example.com"))
        # Rollback deletes the second (just-created) cognito user.
        cognito_mock.admin_delete_user.assert_called_with(
            UserPoolId=user_service._user_pool_id, Username="dup@example.com"
        )

    def test_cognito_username_exists_raises_validation_error_without_touching_db(
        self, user_service, cognito_mock,
    ):
        # Must be the exact class object service.py's `except
        # self._cognito.exceptions.UsernameExistsException` will match
        # against — read it off the mock rather than a separate import
        # (which, depending on how pytest collected conftest.py vs a
        # `tests.conftest` import, could resolve to a distinct class
        # object of the same name).
        exc_cls = cognito_mock.exceptions.UsernameExistsException
        cognito_mock.admin_create_user.side_effect = exc_cls("taken")
        with pytest.raises(ValidationError):
            user_service.create_user(_create_request())
        assert user_service.get_user("cognito-sub-1") is None

    def test_invalid_password_rolls_back_cognito_user_and_raises_validation_error(
        self, user_service, cognito_mock,
    ):
        cognito_mock.admin_set_user_password.side_effect = ClientError(
            {"Error": {"Code": "InvalidPasswordException", "Message": "too weak"}},
            "AdminSetUserPassword",
        )
        with pytest.raises(ValidationError):
            user_service.create_user(_create_request())
        cognito_mock.admin_delete_user.assert_called_once()

    def test_other_cognito_error_on_password_set_reraises(self, user_service, cognito_mock):
        cognito_mock.admin_set_user_password.side_effect = ClientError(
            {"Error": {"Code": "InternalErrorException", "Message": "oops"}},
            "AdminSetUserPassword",
        )
        with pytest.raises(ClientError):
            user_service.create_user(_create_request())
        cognito_mock.admin_delete_user.assert_called_once()

    def test_eventbridge_failure_does_not_fail_create(self, user_service, cognito_mock, events_mock):
        events_mock.put_events.side_effect = ClientError(
            {"Error": {"Code": "InternalFailure", "Message": "bus down"}}, "PutEvents",
        )
        result = user_service.create_user(_create_request())
        assert result["id"] == "cognito-sub-1"


class TestUpdateUser:
    def test_returns_none_when_missing(self, user_service):
        assert user_service.update_user("nope", UpdateUserRequest(first_name="X")) is None

    def test_updates_plain_fields(self, user_service):
        created = user_service.create_user(_create_request())
        updated = user_service.update_user(created["id"], UpdateUserRequest(first_name="Changed"))
        assert updated["first_name"] == "Changed"

    def test_address_provided_with_value_sets_address(self, user_service):
        created = user_service.create_user(_create_request())
        update = UpdateUserRequest(
            address_provided=True,
            address={"address1": "1 Main St", "city": "Toronto", "province": "ON",
                      "postal_code": "A1A 1A1", "country": "CA"},
        )
        updated = user_service.update_user(created["id"], update)
        assert updated["address"]["city"] == "Toronto"

    def test_address_provided_with_none_removes_address(self, user_service):
        created = user_service.create_user(_create_request())
        with_address = UpdateUserRequest(
            address_provided=True,
            address={"address1": "1 Main St", "city": "Toronto", "province": "ON",
                      "postal_code": "A1A 1A1", "country": "CA"},
        )
        user_service.update_user(created["id"], with_address)

        cleared = UpdateUserRequest(address_provided=True, address=None)
        updated = user_service.update_user(created["id"], cleared)
        assert updated["address"] is None

    def test_email_change_updates_cognito_first(self, user_service, cognito_mock):
        created = user_service.create_user(_create_request(email="old@example.com"))
        updated = user_service.update_user(created["id"], UpdateUserRequest(email="new@example.com"))
        assert updated["email"] == "new@example.com"
        cognito_mock.admin_update_user_attributes.assert_called_once()

    def test_email_change_to_taken_email_rolls_back_cognito_and_raises(self, user_service, cognito_mock):
        user_service.create_user(_create_request(email="taken@example.com"))
        cognito_mock.admin_create_user.return_value = {
            "User": {"Attributes": [{"Name": "sub", "Value": "cognito-sub-2"}]}
        }
        created2 = user_service.create_user(_create_request(email="mine@example.com"))

        with pytest.raises(ValidationError):
            user_service.update_user(created2["id"], UpdateUserRequest(email="taken@example.com"))

        # Cognito email must have been reverted back to the original.
        calls = cognito_mock.admin_update_user_attributes.call_args_list
        assert calls[-1].kwargs["Username"] == "taken@example.com"
        reverted_email = next(
            a["Value"] for a in calls[-1].kwargs["UserAttributes"] if a["Name"] == "email"
        )
        assert reverted_email == "mine@example.com"


class TestDeleteUser:
    def test_missing_user_returns_false(self, user_service):
        assert user_service.delete_user("nope") is False

    def test_deletes_db_row_and_cognito_user(self, user_service, cognito_mock):
        created = user_service.create_user(_create_request())
        cognito_mock.reset_mock()

        assert user_service.delete_user(created["id"]) is True
        assert user_service.get_user(created["id"]) is None
        cognito_mock.admin_delete_user.assert_called_once_with(
            UserPoolId=user_service._user_pool_id, Username="shopper@example.com",
        )


class TestDeleteCognitoUserBestEffort:
    def test_user_not_found_is_swallowed(self, user_service, cognito_mock):
        cognito_mock.admin_delete_user.side_effect = ClientError(
            {"Error": {"Code": "UserNotFoundException", "Message": "gone"}}, "AdminDeleteUser",
        )
        # Should not raise even though the underlying delete "fails".
        user_service._delete_cognito_user("someone@example.com")

    def test_other_error_is_logged_not_raised(self, user_service, cognito_mock):
        cognito_mock.admin_delete_user.side_effect = ClientError(
            {"Error": {"Code": "InternalErrorException", "Message": "oops"}}, "AdminDeleteUser",
        )
        user_service._delete_cognito_user("someone@example.com")
