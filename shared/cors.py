"""
CORS helper shared by every browser-facing API Lambda (products, orders,
users, payments_api).

In dev, any origin is allowed — the storefront/admin frontends get deployed
to preview URLs and localhost ports that don't fit a fixed list, so we just
echo back whatever Origin the browser sent.

Outside dev, both the apex (https://chonkycat.ca) and any subdomain
(https://admin.chonkycat.ca, https://www.chonkycat.ca, ...) are allowed, plus
branch preview URLs on the storefront's Amplify app
(https://main.<AMPLIFY_APP_ID>.amplifyapp.com, ...) since those are used
before a branch's custom domain is wired up. The Amplify app id is read from
the AMPLIFY_APP_ID env var rather than hardcoded, since the app (and its id)
gets recreated occasionally — updating the env var doesn't require rebuilding
or redeploying this layer.
CORS has no wildcard-subdomain syntax for Access-Control-Allow-Origin — it's
either a literal "*" or one exact origin — so this can't be done with
API Gateway's built-in CORS config (a single static value). Instead we match
the request's Origin header against the pattern ourselves and echo it back
verbatim when it matches. No match means no Access-Control-Allow-Origin
header, which the browser treats as a CORS failure.
"""

import os
import re

_CHONKYCAT_ORIGIN_RE = re.compile(r"^https://([a-z0-9-]+\.)*chonkycat\.ca$")

_amplify_app_id = os.environ.get("AMPLIFY_APP_ID")
_AMPLIFY_ORIGIN_RE = (
    re.compile(rf"^https://[a-z0-9-]+\.{re.escape(_amplify_app_id)}\.amplifyapp\.com$")
    if _amplify_app_id
    else None
)

DEFAULT_ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
DEFAULT_ALLOW_HEADERS = "Content-Type, Authorization, X-Guest-Id"


def _get_origin(event: dict) -> str | None:
    headers = (event or {}).get("headers") or {}
    for key, value in headers.items():
        if key.lower() == "origin":
            return value
    return None


def _get_method(event: dict) -> str | None:
    if "httpMethod" in (event or {}):
        return event["httpMethod"]
    return (event or {}).get("requestContext", {}).get("http", {}).get("method")


def resolve_allow_origin(event: dict) -> str | None:
    origin = _get_origin(event)

    if os.environ.get("ENVIRONMENT", "dev") == "dev":
        return origin or "*"

    if origin and _CHONKYCAT_ORIGIN_RE.match(origin):
        return origin

    if origin and _AMPLIFY_ORIGIN_RE and _AMPLIFY_ORIGIN_RE.match(origin):
        return origin

    return None


def build_cors_headers(event: dict, methods: str = DEFAULT_ALLOW_METHODS) -> dict:
    headers = {
        "Access-Control-Allow-Methods": methods,
        "Access-Control-Allow-Headers": DEFAULT_ALLOW_HEADERS,
    }
    allow_origin = resolve_allow_origin(event)
    if allow_origin:
        headers["Access-Control-Allow-Origin"] = allow_origin
        # Response varies by request Origin (echoed back) — tell caches/CDNs.
        headers["Vary"] = "Origin"
    return headers


def is_preflight(event: dict) -> bool:
    return _get_method(event) == "OPTIONS"


def preflight_response(event: dict, methods: str = DEFAULT_ALLOW_METHODS) -> dict:
    return {
        "statusCode": 204,
        "headers": build_cors_headers(event, methods),
        "body": "",
    }
