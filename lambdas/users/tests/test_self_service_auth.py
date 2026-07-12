from tests.conftest import body_of


class TestGetUserSelfOnly:
    def test_no_authorizer_claims_rejected(self, fake_service, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(make_event("GET", user_id="u1"), None)
        assert resp["statusCode"] == 403
        assert fake_service.get_user_calls == []

    def test_mismatched_sub_rejected(self, fake_service, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("GET", user_id="u1", authenticated_sub="someone-else"), None
        )
        assert resp["statusCode"] == 403
        assert fake_service.get_user_calls == []

    def test_matching_sub_allowed(self, fake_service, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("GET", user_id="u1", authenticated_sub="u1"), None
        )
        assert resp["statusCode"] == 200
        assert body_of(resp)["user"]["first_name"] == "Old"
        assert fake_service.get_user_calls == ["u1"]


class TestUpdateUserSelfOnly:
    def test_mismatched_sub_rejected(self, fake_service, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("PUT", user_id="u1", authenticated_sub="someone-else", body={"first_name": "New"}),
            None,
        )
        assert resp["statusCode"] == 403
        assert fake_service.update_user_calls == []

    def test_matching_sub_can_update_profile_fields(self, fake_service, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("PUT", user_id="u1", authenticated_sub="u1",
                       body={"first_name": "New", "last_name": "Name", "phone": "+1-555-0100"}),
            None,
        )
        assert resp["statusCode"] == 200
        assert body_of(resp)["user"]["first_name"] == "New"
        assert fake_service.update_user_calls[0][0] == "u1"

    def test_cannot_self_promote_role(self, fake_service, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("PUT", user_id="u1", authenticated_sub="u1", body={"role": "admin"}),
            None,
        )
        assert resp["statusCode"] == 403
        assert fake_service.update_user_calls == []

    def test_cannot_self_edit_email(self, fake_service, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("PUT", user_id="u1", authenticated_sub="u1", body={"email": "new@example.com"}),
            None,
        )
        assert resp["statusCode"] == 403
        assert fake_service.update_user_calls == []

    def test_cannot_self_edit_status(self, fake_service, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("PUT", user_id="u1", authenticated_sub="u1", body={"status": "suspended"}),
            None,
        )
        assert resp["statusCode"] == 403
        assert fake_service.update_user_calls == []


class TestAdminGetUser:
    def test_no_self_check_required(self, fake_service, make_event):
        """The admin route has no authenticated_sub at all — a self-only
        check would reject this the same way TestGetUserSelfOnly's
        no-authorizer-claims case does; the admin route must not apply it."""
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("GET", user_id="u1", resource="/admin/users/{userId}"), None
        )
        assert resp["statusCode"] == 200
        assert fake_service.get_user_calls == ["u1"]

    def test_can_view_a_different_user(self, fake_service, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event(
                "GET", user_id="someone-else", authenticated_sub="admin-sub",
                resource="/admin/users/{userId}",
            ),
            None,
        )
        assert resp["statusCode"] == 200
        assert fake_service.get_user_calls == ["someone-else"]

    def test_plain_users_path_still_requires_self(self, fake_service, make_event):
        """Sanity check that the admin flag is actually resource-gated, not
        just always on now."""
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("GET", user_id="u1", resource="/users/{userId}"), None
        )
        assert resp["statusCode"] == 403
        assert fake_service.get_user_calls == []


class TestAdminUpdateUser:
    def test_no_self_check_required(self, fake_service, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event(
                "PUT", user_id="someone-else", body={"first_name": "New"},
                resource="/admin/users/{userId}",
            ),
            None,
        )
        assert resp["statusCode"] == 200
        assert fake_service.update_user_calls[0][0] == "someone-else"

    def test_can_change_role_status_and_email(self, fake_service, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event(
                "PUT", user_id="u1",
                body={"role": "admin", "status": "suspended", "email": "new@example.com"},
                resource="/admin/users/{userId}",
            ),
            None,
        )
        assert resp["statusCode"] == 200
        _, update = fake_service.update_user_calls[0]
        assert update.role == "admin"
        assert update.status == "suspended"
        assert update.email == "new@example.com"

    def test_plain_users_path_still_requires_self(self, fake_service, make_event):
        import lambda_handler
        resp = lambda_handler.lambda_handler(
            make_event("PUT", user_id="u1", body={"role": "admin"}, resource="/users/{userId}"),
            None,
        )
        assert resp["statusCode"] == 403
        assert fake_service.update_user_calls == []
