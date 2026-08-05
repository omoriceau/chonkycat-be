"""
Tests for shared/cors.py's origin-matching logic.

Every other test in this suite runs with ENVIRONMENT unset (defaults to
"dev" — allow any origin), so none of them exercise the non-dev branches:
matching against chonkycat.ca / its subdomains, or an Amplify preview URL
via AMPLIFY_APP_ID. Covered here instead, directly against the shared
module (not through lambda_handler), since that's the only place this
logic runs regardless of which Lambda calls it.
"""

import re

from shared import cors


def _event(origin=None):
    return {"headers": {"Origin": origin}} if origin else {"headers": {}}


class TestResolveAllowOriginDev:
    def test_dev_environment_allows_any_origin(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        assert cors.resolve_allow_origin(_event("https://evil.example.com")) == "https://evil.example.com"

    def test_dev_environment_falls_back_to_wildcard_with_no_origin(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        assert cors.resolve_allow_origin(_event()) == "*"


class TestResolveAllowOriginNonDev:
    def test_allows_chonkycat_apex(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        origin = "https://chonkycat.ca"
        assert cors.resolve_allow_origin(_event(origin)) == origin

    def test_allows_chonkycat_subdomain(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        origin = "https://admin.chonkycat.ca"
        assert cors.resolve_allow_origin(_event(origin)) == origin

    def test_rejects_unrelated_origin(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        assert cors.resolve_allow_origin(_event("https://evil.example.com")) is None

    def test_rejects_no_origin_header(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        assert cors.resolve_allow_origin(_event()) is None

    def test_rejects_amplify_origin_when_app_id_not_configured(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setattr(cors, "_AMPLIFY_ORIGIN_RE", None)
        origin = "https://main.d123abc456.amplifyapp.com"
        assert cors.resolve_allow_origin(_event(origin)) is None

    def test_allows_amplify_preview_when_app_id_configured(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setattr(
            cors, "_AMPLIFY_ORIGIN_RE", re.compile(r"^https://[a-z0-9-]+\.d123abc456\.amplifyapp\.com$")
        )
        origin = "https://main.d123abc456.amplifyapp.com"
        assert cors.resolve_allow_origin(_event(origin)) == origin

    def test_rejects_amplify_origin_for_a_different_app_id(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setattr(
            cors, "_AMPLIFY_ORIGIN_RE", re.compile(r"^https://[a-z0-9-]+\.d123abc456\.amplifyapp\.com$")
        )
        origin = "https://main.d999different.amplifyapp.com"
        assert cors.resolve_allow_origin(_event(origin)) is None


class TestBuildCorsHeaders:
    def test_includes_allow_origin_and_vary_when_matched(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        headers = cors.build_cors_headers(_event("https://chonkycat.ca"))
        assert headers["Access-Control-Allow-Origin"] == "https://chonkycat.ca"
        assert headers["Vary"] == "Origin"

    def test_omits_allow_origin_and_vary_when_not_matched(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        headers = cors.build_cors_headers(_event("https://evil.example.com"))
        assert "Access-Control-Allow-Origin" not in headers
        assert "Vary" not in headers
