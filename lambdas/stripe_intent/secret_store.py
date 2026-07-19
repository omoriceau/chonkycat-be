"""
secrets.py — generic AWS Secrets Manager lookup, cached per warm container.

Fetches an actual secret *value* given a secret *name*. The Lambda's
execution role must be granted secretsmanager:GetSecretValue for that
specific secret ARN (see template.yaml's Policies block for this function).

Handles both plain-string secrets and JSON secrets with a single key
(e.g. {"stripe_webhook_secret": "whsec_..."}) — Secrets Manager supports
both, and which one exists depends on how the secret was created.
"""

import json
from functools import lru_cache

import boto3

_client = boto3.client("secretsmanager")


@lru_cache(maxsize=10)
def get_secret(secret_name: str) -> str:
    response = _client.get_secret_value(SecretId=secret_name)

    if "SecretString" not in response:
        raise RuntimeError(f"Secret '{secret_name}' has no SecretString value (binary secrets aren't supported here)")

    raw = response["SecretString"]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw  # plain-string secret

    if isinstance(parsed, dict) and len(parsed) == 1:
        return next(iter(parsed.values()))

    # JSON with multiple keys — caller needs a specific field, not this helper.
    raise RuntimeError(
        f"Secret '{secret_name}' is a multi-key JSON object; get_secret() only "
        f"supports plain-string or single-key JSON secrets."
    )