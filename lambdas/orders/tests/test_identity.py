"""
Direct unit tests for identity.py's token verification and identity
resolution logic. JWKS/network calls are mocked out (via monkeypatching
identity._get_jwk_client / identity.jwt.decode) so these exercise the real
branch logic without needing a live Cognito pool or real signed JWTs.
"""
from unittest.mock import MagicMock

import pytest

import identity
from identity import (
    IdentityError,
    _get_jwk_client,
    _verify_token,
    resolve_authenticated_user_id,
    resolve_user_id,
)


class TestVerifyToken:
    def test_no_pool_configured_raises(self, monkeypatch):
        monkeypatch.setattr(identity, "_USER_POOL_ID", None)
        with pytest.raises(IdentityError, match="not configured"):
            _verify_token("sometoken")

    def test_valid_token_returns_claims(self, monkeypatch):
        monkeypatch.setattr(identity, "_USER_POOL_ID", "pool-123")
        monkeypatch.setattr(identity, "_APP_CLIENT_ID", None)
        fake_client = MagicMock()
        fake_client.get_signing_key_from_jwt.return_value = MagicMock(key="fake-key")
        monkeypatch.setattr(identity, "_get_jwk_client", lambda: fake_client)
        monkeypatch.setattr(identity.jwt, "decode", lambda *a, **k: {"sub": "user-1", "token_use": "id"})

        claims = _verify_token("tok")
        assert claims["sub"] == "user-1"

    def test_unexpected_token_use_raises(self, monkeypatch):
        monkeypatch.setattr(identity, "_USER_POOL_ID", "pool-123")
        monkeypatch.setattr(identity, "_APP_CLIENT_ID", None)
        fake_client = MagicMock()
        fake_client.get_signing_key_from_jwt.return_value = MagicMock(key="fake-key")
        monkeypatch.setattr(identity, "_get_jwk_client", lambda: fake_client)
        monkeypatch.setattr(identity.jwt, "decode", lambda *a, **k: {"sub": "user-1", "token_use": "refresh"})

        with pytest.raises(IdentityError, match="Unexpected token_use"):
            _verify_token("tok")

    def test_app_client_id_mismatch_on_id_token_raises(self, monkeypatch):
        monkeypatch.setattr(identity, "_USER_POOL_ID", "pool-123")
        monkeypatch.setattr(identity, "_APP_CLIENT_ID", "expected-client")
        fake_client = MagicMock()
        fake_client.get_signing_key_from_jwt.return_value = MagicMock(key="fake-key")
        monkeypatch.setattr(identity, "_get_jwk_client", lambda: fake_client)
        monkeypatch.setattr(
            identity.jwt, "decode",
            lambda *a, **k: {"sub": "u1", "token_use": "id", "aud": "other-client"},
        )

        with pytest.raises(IdentityError, match="not issued for this app client"):
            _verify_token("tok")

    def test_app_client_id_match_on_access_token_succeeds(self, monkeypatch):
        monkeypatch.setattr(identity, "_USER_POOL_ID", "pool-123")
        monkeypatch.setattr(identity, "_APP_CLIENT_ID", "expected-client")
        fake_client = MagicMock()
        fake_client.get_signing_key_from_jwt.return_value = MagicMock(key="fake-key")
        monkeypatch.setattr(identity, "_get_jwk_client", lambda: fake_client)
        monkeypatch.setattr(
            identity.jwt, "decode",
            lambda *a, **k: {"sub": "u1", "token_use": "access", "client_id": "expected-client"},
        )

        claims = _verify_token("tok")
        assert claims["sub"] == "u1"

    def test_app_client_id_mismatch_on_access_token_raises(self, monkeypatch):
        monkeypatch.setattr(identity, "_USER_POOL_ID", "pool-123")
        monkeypatch.setattr(identity, "_APP_CLIENT_ID", "expected-client")
        fake_client = MagicMock()
        fake_client.get_signing_key_from_jwt.return_value = MagicMock(key="fake-key")
        monkeypatch.setattr(identity, "_get_jwk_client", lambda: fake_client)
        monkeypatch.setattr(
            identity.jwt, "decode",
            lambda *a, **k: {"sub": "u1", "token_use": "access", "client_id": "wrong-client"},
        )

        with pytest.raises(IdentityError, match="not issued for this app client"):
            _verify_token("tok")


class TestGetJwkClient:
    def test_caches_across_calls(self, monkeypatch):
        monkeypatch.setattr(identity, "_jwk_client", None)
        monkeypatch.setattr(identity, "_USER_POOL_ID", "pool-123")
        created_urls = []

        class FakePyJWKClient:
            def __init__(self, url):
                created_urls.append(url)

        monkeypatch.setattr(identity, "PyJWKClient", FakePyJWKClient)

        first = _get_jwk_client()
        second = _get_jwk_client()

        assert first is second
        assert len(created_urls) == 1
        assert "pool-123" in created_urls[0]
        assert "jwks.json" in created_urls[0]


class TestResolveUserId:
    def test_bearer_token_resolves_to_sub(self, monkeypatch):
        monkeypatch.setattr(identity, "_verify_token", lambda token: {"sub": "cognito-user-1"})
        user_id = resolve_user_id({"headers": {"Authorization": "Bearer abc.def.ghi"}})
        assert user_id == "cognito-user-1"

    def test_bearer_prefix_is_case_insensitive_and_stripped(self, monkeypatch):
        seen = {}

        def fake_verify(token):
            seen["token"] = token
            return {"sub": "u1"}

        monkeypatch.setattr(identity, "_verify_token", fake_verify)
        user_id = resolve_user_id({"headers": {"authorization": "bearer sometoken"}})
        assert user_id == "u1"
        assert seen["token"] == "sometoken"

    def test_non_bearer_auth_header_passed_through_whole(self, monkeypatch):
        seen = {}

        def fake_verify(token):
            seen["token"] = token
            return {"sub": "u1"}

        monkeypatch.setattr(identity, "_verify_token", fake_verify)
        resolve_user_id({"headers": {"Authorization": "raw-token-no-prefix"}})
        assert seen["token"] == "raw-token-no-prefix"

    def test_invalid_token_wraps_exception_as_identity_error(self, monkeypatch):
        def boom(token):
            raise ValueError("bad signature")

        monkeypatch.setattr(identity, "_verify_token", boom)
        with pytest.raises(IdentityError, match="Invalid Authorization token"):
            resolve_user_id({"headers": {"Authorization": "Bearer bad"}})

    def test_identity_error_from_verify_propagates_unwrapped(self, monkeypatch):
        def boom(token):
            raise IdentityError("nope, not valid")

        monkeypatch.setattr(identity, "_verify_token", boom)
        with pytest.raises(IdentityError, match="nope, not valid"):
            resolve_user_id({"headers": {"Authorization": "Bearer bad"}})

    def test_guest_header_fallback(self):
        user_id = resolve_user_id({"headers": {"X-Guest-Id": "abc-123"}})
        assert user_id == "guest_abc-123"

    def test_guest_header_is_case_insensitive(self):
        user_id = resolve_user_id({"headers": {"x-guest-id": "abc-123"}})
        assert user_id == "guest_abc-123"

    def test_missing_both_raises(self):
        with pytest.raises(IdentityError, match="Authorization token or an X-Guest-Id"):
            resolve_user_id({"headers": {}})

    def test_blank_guest_id_raises(self):
        with pytest.raises(IdentityError):
            resolve_user_id({"headers": {"X-Guest-Id": "   "}})

    def test_none_event_raises(self):
        with pytest.raises(IdentityError):
            resolve_user_id(None)


class TestResolveAuthenticatedUserId:
    def test_returns_sub_from_authorizer_claims(self):
        event = {"requestContext": {"authorizer": {"claims": {"sub": "sub-1"}}}}
        assert resolve_authenticated_user_id(event) == "sub-1"

    def test_missing_request_context_raises(self):
        with pytest.raises(IdentityError, match="missing Cognito authorizer claims"):
            resolve_authenticated_user_id({})

    def test_missing_sub_claim_raises(self):
        event = {"requestContext": {"authorizer": {"claims": {}}}}
        with pytest.raises(IdentityError):
            resolve_authenticated_user_id(event)

    def test_none_event_raises(self):
        with pytest.raises(IdentityError):
            resolve_authenticated_user_id(None)
