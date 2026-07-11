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
