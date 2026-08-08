"""
Tests for lambda_handler.py's top-level dispatch and the list/create/delete
routes (the self-service get/update routes are covered by
test_self_service_auth.py). Uses the fake_service fixture (see
tests/conftest.py) — a stand-in for UserService that records calls and can
be told to raise, so these tests can drive lambda_handler's own
validation/error-mapping logic without touching real AWS.
"""

from botocore.exceptions import ClientError

from tests.conftest import body_of


class TestDispatch:
    def test_preflight_request(self, fake_service, make_event):
        import lambda_handler

        event = make_event("OPTIONS")
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 204

    def test_unsupported_method(self, fake_service, make_event):
        import lambda_handler

        resp = lambda_handler.lambda_handler(make_event("PATCH"), None)
        assert resp["statusCode"] == 405

    def test_get_without_path_id_lists_users(self, fake_service, make_event):
        import lambda_handler

        resp = lambda_handler.lambda_handler(make_event("GET"), None)
        assert resp["statusCode"] == 200
        assert fake_service.list_users_calls == [(50, 0, None, None)]

    def test_post_dispatches_to_create(self, fake_service, make_event):
        import lambda_handler

        resp = lambda_handler.lambda_handler(
            make_event("POST", body={"email": "a@example.com", "password": "Correct-Horse-Battery-9!"}),
            None,
        )
        assert resp["statusCode"] == 201
        assert len(fake_service.create_user_calls) == 1

    def test_delete_dispatches(self, fake_service, make_event):
        import lambda_handler

        resp = lambda_handler.lambda_handler(make_event("DELETE", user_id="u1"), None)
        assert resp["statusCode"] == 200
        assert fake_service.delete_user_calls == ["u1"]


class TestGetService:
    def test_lazily_constructs_and_caches_service(self, monkeypatch):
        import lambda_handler

        monkeypatch.setattr(lambda_handler, "_service", None)
        first = lambda_handler._get_service()
        second = lambda_handler._get_service()
        assert first is second


class TestListUsersRoute:
    def test_defaults(self, fake_service, make_event):
        import lambda_handler

        resp = lambda_handler.lambda_handler(make_event("GET"), None)
        assert resp["statusCode"] == 200
        assert body_of(resp) == fake_service.list_users_return

    def test_passes_through_query_params(self, fake_service, make_event):
        import lambda_handler

        event = make_event("GET", query_params={"limit": "10", "offset": "5", "role": "admin", "status": "active"})
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 200
        assert fake_service.list_users_calls == [(10, 5, "admin", "active")]

    def test_non_integer_limit_is_rejected(self, fake_service, make_event):
        import lambda_handler

        event = make_event("GET", query_params={"limit": "not-a-number"})
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 400
        assert fake_service.list_users_calls == []

    def test_service_error_returns_500(self, fake_service, make_event):
        import lambda_handler

        fake_service.list_users_raises = RuntimeError("boom")
        resp = lambda_handler.lambda_handler(make_event("GET"), None)
        assert resp["statusCode"] == 500


class TestCreateUserRoute:
    VALID_BODY = {"email": "new@example.com", "password": "Correct-Horse-Battery-9!"}

    def test_success(self, fake_service, make_event):
        import lambda_handler

        resp = lambda_handler.lambda_handler(make_event("POST", body=self.VALID_BODY), None)
        assert resp["statusCode"] == 201
        assert body_of(resp)["user"] == fake_service.create_user_return

    def test_invalid_json_body(self, fake_service, make_event):
        import lambda_handler

        event = make_event("POST", raw_body="{not json")
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 400
        assert fake_service.create_user_calls == []

    def test_missing_required_field_is_422(self, fake_service, make_event):
        import lambda_handler

        resp = lambda_handler.lambda_handler(make_event("POST", body={"email": "new@example.com"}), None)
        assert resp["statusCode"] == 422
        assert fake_service.create_user_calls == []

    def test_duplicate_email_is_409(self, fake_service, make_event):
        import lambda_handler
        from models import ValidationError

        fake_service.create_user_raises = ValidationError("A user with email 'new@example.com' already exists")
        resp = lambda_handler.lambda_handler(make_event("POST", body=self.VALID_BODY), None)
        assert resp["statusCode"] == 409

    def test_infra_error_is_500(self, fake_service, make_event):
        import lambda_handler

        fake_service.create_user_raises = ClientError(
            {"Error": {"Code": "InternalErrorException", "Message": "down"}}, "AdminCreateUser",
        )
        resp = lambda_handler.lambda_handler(make_event("POST", body=self.VALID_BODY), None)
        assert resp["statusCode"] == 500

    def test_unexpected_error_is_500(self, fake_service, make_event):
        import lambda_handler

        fake_service.create_user_raises = RuntimeError("boom")
        resp = lambda_handler.lambda_handler(make_event("POST", body=self.VALID_BODY), None)
        assert resp["statusCode"] == 500


class TestUpdateUserRouteErrors:
    """Extends TestUpdateUserSelfOnly (test_self_service_auth.py) with the
    non-auth error paths of _handle_update_user."""

    def test_invalid_user_id(self, fake_service, make_event):
        import lambda_handler

        event = make_event("PUT", authenticated_sub="u1", body={"first_name": "X"})
        event["pathParameters"] = {}
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 400

    def test_invalid_json_body(self, fake_service, make_event):
        import lambda_handler

        event = make_event("PUT", user_id="u1", authenticated_sub="u1", raw_body="{not json")
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 400

    def test_empty_update_is_422(self, fake_service, make_event):
        import lambda_handler

        resp = lambda_handler.lambda_handler(
            make_event("PUT", user_id="u1", authenticated_sub="u1", body={}), None,
        )
        assert resp["statusCode"] == 422

    def test_user_not_found_is_404(self, fake_service, make_event):
        import lambda_handler

        fake_service.update_user_return = None
        resp = lambda_handler.lambda_handler(
            make_event("PUT", user_id="u1", authenticated_sub="u1", body={"first_name": "X"}), None,
        )
        assert resp["statusCode"] == 404

    def test_duplicate_email_admin_route_is_409(self, fake_service, make_event):
        import lambda_handler
        from models import ValidationError

        fake_service.update_user_raises = ValidationError("dup")
        resp = lambda_handler.lambda_handler(
            make_event("PUT", user_id="u1", body={"email": "x@example.com"}, resource="/admin/users/{userId}"),
            None,
        )
        assert resp["statusCode"] == 409

    def test_infra_error_is_500(self, fake_service, make_event):
        import lambda_handler

        fake_service.update_user_raises = ClientError(
            {"Error": {"Code": "InternalErrorException", "Message": "down"}}, "UpdateItem",
        )
        resp = lambda_handler.lambda_handler(
            make_event("PUT", user_id="u1", authenticated_sub="u1", body={"first_name": "X"}), None,
        )
        assert resp["statusCode"] == 500

    def test_unexpected_error_is_500(self, fake_service, make_event):
        import lambda_handler

        fake_service.update_user_raises = RuntimeError("boom")
        resp = lambda_handler.lambda_handler(
            make_event("PUT", user_id="u1", authenticated_sub="u1", body={"first_name": "X"}), None,
        )
        assert resp["statusCode"] == 500


class TestGetUserRouteErrors:
    def test_invalid_user_id(self, fake_service, make_event):
        """A present-but-blank userId is truthy enough to clear
        lambda_handler()'s own has_path_id gate (bool("   ") is True), so
        it reaches _handle_get_user's own _parse_user_id validation."""
        import lambda_handler

        event = make_event("GET", authenticated_sub="u1")
        event["pathParameters"] = {"userId": "   "}
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 400

    def test_not_found_is_404(self, fake_service, make_event):
        import lambda_handler

        fake_service.get_user_return = None
        resp = lambda_handler.lambda_handler(
            make_event("GET", user_id="u1", authenticated_sub="u1"), None,
        )
        assert resp["statusCode"] == 404

    def test_unexpected_error_is_500(self, fake_service, make_event):
        import lambda_handler

        fake_service.get_user_raises = RuntimeError("boom")
        resp = lambda_handler.lambda_handler(
            make_event("GET", user_id="u1", authenticated_sub="u1"), None,
        )
        assert resp["statusCode"] == 500


class TestDeleteUserRoute:
    def test_invalid_user_id(self, fake_service, make_event):
        import lambda_handler

        event = make_event("DELETE")
        event["pathParameters"] = {}
        resp = lambda_handler.lambda_handler(event, None)
        assert resp["statusCode"] == 400

    def test_success(self, fake_service, make_event):
        import lambda_handler

        resp = lambda_handler.lambda_handler(make_event("DELETE", user_id="u1"), None)
        assert resp["statusCode"] == 200
        assert body_of(resp)["user_id"] == "u1"

    def test_not_found_is_404(self, fake_service, make_event):
        import lambda_handler

        fake_service.delete_user_return = False
        resp = lambda_handler.lambda_handler(make_event("DELETE", user_id="u1"), None)
        assert resp["statusCode"] == 404

    def test_unexpected_error_is_500(self, fake_service, make_event):
        import lambda_handler

        fake_service.delete_user_raises = RuntimeError("boom")
        resp = lambda_handler.lambda_handler(make_event("DELETE", user_id="u1"), None)
        assert resp["statusCode"] == 500


def test_log_safe_strips_crlf():
    import lambda_handler

    assert lambda_handler._log_safe("abc\r\ndef") == "abcdef"
