"""
lambdas/orders/identity.py

Resolves the caller's identity for the cart endpoints, which have to accept
both signed-in shoppers and anonymous guests on the *same* routes. API
Gateway's Cognito authorizer is all-or-nothing per route, so it's only
attached to POST /cart/claim (see template.yaml) — everywhere else, this
module does its own lightweight verification instead:

  - Authorization: Bearer <Cognito token> present and valid -> the token's
    `sub` claim is the user_id.
  - Otherwise -> the caller must send X-Guest-Id (a client-generated UUID);
    user_id becomes "guest_<that id>". The "guest_" prefix keeps guest and
    real (Cognito sub) ids visually and structurally distinct everywhere,
    including in the /cart/claim merge logic.

Environment Variables:
  - CUSTOMER_COGNITO_USER_POOL_ID   Pool backing the storefront's Authenticator
  - CUSTOMER_COGNITO_APP_CLIENT_ID  App client id (optional aud/client_id check)
"""

import os

import jwt
from jwt import PyJWKClient

_USER_POOL_ID = os.environ.get("CUSTOMER_COGNITO_USER_POOL_ID")
_APP_CLIENT_ID = os.environ.get("CUSTOMER_COGNITO_APP_CLIENT_ID")
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Cached across warm invocations — avoids refetching the pool's JWKS on
# every request.
_jwk_client: PyJWKClient | None = None


class IdentityError(Exception):
    pass


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        jwks_url = (
            f"https://cognito-idp.{_AWS_REGION}.amazonaws.com/"
            f"{_USER_POOL_ID}/.well-known/jwks.json"
        )
        _jwk_client = PyJWKClient(jwks_url)
    return _jwk_client


def _get_header(event: dict, name: str) -> str | None:
    headers = (event or {}).get("headers") or {}
    lname = name.lower()
    for key, value in headers.items():
        if key.lower() == lname:
            return value
    return None


def _verify_token(token: str) -> dict:
    if not _USER_POOL_ID:
        raise IdentityError("Server is not configured with a customer Cognito user pool")

    signing_key = _get_jwk_client().get_signing_key_from_jwt(token).key
    claims = jwt.decode(
        token,
        signing_key,
        algorithms=["RS256"],
        issuer=f"https://cognito-idp.{_AWS_REGION}.amazonaws.com/{_USER_POOL_ID}",
        options={"require": ["exp", "sub"], "verify_aud": False},
    )

    token_use = claims.get("token_use")
    if token_use not in ("id", "access"):
        raise IdentityError(f"Unexpected token_use claim: {token_use!r}")

    # Cognito puts the app client id under `aud` on ID tokens but
    # `client_id` on access tokens — check whichever applies.
    if _APP_CLIENT_ID:
        actual_client = claims.get("aud") if token_use == "id" else claims.get("client_id")
        if actual_client != _APP_CLIENT_ID:
            raise IdentityError("Token was not issued for this app client")

    return claims


def resolve_user_id(event: dict) -> str:
    """
    Returns the resolved user_id for a cart request. Raises IdentityError
    if neither a valid bearer token nor an X-Guest-Id header is present.
    """
    auth_header = _get_header(event, "authorization")
    if auth_header:
        token = auth_header[7:] if auth_header.lower().startswith("bearer ") else auth_header
        try:
            claims = _verify_token(token)
        except IdentityError:
            raise
        except Exception as e:
            raise IdentityError(f"Invalid Authorization token: {e}") from e
        return claims["sub"]

    guest_id = _get_header(event, "x-guest-id")
    if guest_id and guest_id.strip():
        return f"guest_{guest_id.strip()}"

    raise IdentityError("Request must include either an Authorization token or an X-Guest-Id header")


def resolve_authenticated_user_id(event: dict) -> str:
    """
    For routes sitting behind API Gateway's Cognito authorizer (currently
    just POST /cart/claim) — API Gateway has already verified the token by
    the time the Lambda runs, so this just reads the `sub` it extracted
    rather than re-verifying anything. Never trust a client-supplied user id
    for this route; it's what stops a caller from claiming someone else's
    cart.
    """
    claims = ((event or {}).get("requestContext") or {}).get("authorizer", {}).get("claims", {})
    sub = claims.get("sub")
    if not sub:
        raise IdentityError("No authenticated identity on request (missing Cognito authorizer claims)")
    return sub
