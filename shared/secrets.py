"""AWS Secrets Manager utilities for retrieving sensitive configuration."""

import json
import logging
import os
from functools import lru_cache

import boto3

logger = logging.getLogger(__name__)

# Secrets cache with TTL handled by boto3's built-in caching
_secrets_client = None


def get_secrets_client():
    """Get or create AWS Secrets Manager client."""
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = boto3.client("secretsmanager")
    return _secrets_client


@lru_cache(maxsize=128)
def get_secret(secret_name: str) -> dict:
    """
    Retrieve a secret from AWS Secrets Manager.
    
    Args:
        secret_name: The name or ARN of the secret
        
    Returns:
        dict: Parsed secret value (if JSON) or raw string
        
    Raises:
        boto3.exceptions.Boto3Error: If secret retrieval fails
    """
    try:
        client = get_secrets_client()
        response = client.get_secret_value(SecretId=secret_name)
        
        # Try to parse as JSON, fall back to string
        secret_value = response.get("SecretString")
        if secret_value:
            try:
                return json.loads(secret_value)
            except json.JSONDecodeError:
                return {"value": secret_value}
        
        return {"value": response.get("SecretBinary")}
    except Exception as e:
        logger.error(f"Failed to retrieve secret '{secret_name}': {str(e)}")
        raise


def get_db_password() -> str:
    """
    Get RDS master password from AWS Secrets Manager.
    Falls back to environment variable for local development.
    
    Returns:
        str: The database password
    """
    secret_name = os.getenv("DB_PASSWORD_SECRET_NAME", "chonky/dev/db_pass")
    
    # Try AWS Secrets Manager first
    try:
        secret = get_secret(secret_name)
        if isinstance(secret, dict) and "password" in secret:
            return secret["password"]
        if isinstance(secret, dict) and "value" in secret:
            return secret["value"]
        return str(secret)
    except Exception as e:
        logger.warning(f"Failed to get secret from AWS Secrets Manager: {e}")
        # Fall back to environment variable for local development
        password = os.getenv("DB_PASSWORD")
        if not password:
            raise ValueError(
                f"DB_PASSWORD not available. Set DB_PASSWORD env var or configure AWS Secrets Manager."
            )
        return password


def get_stripe_key() -> str:
    """
    Get Stripe secret key from AWS Secrets Manager.
    Falls back to environment variable for local development.
    
    Returns:
        str: The Stripe secret key
    """
    secret_name = os.getenv("STRIPE_SECRET_KEY_SECRET_NAME", "chonky/dev/stripe_secret_key")
    
    # Try AWS Secrets Manager first
    try:
        secret = get_secret(secret_name)
        if isinstance(secret, dict) and "key" in secret:
            return secret["key"]
        if isinstance(secret, dict) and "value" in secret:
            return secret["value"]
        if isinstance(secret, dict) and "stripe_secret_key" in secret:
            return secret["stripe_secret_key"]
        return str(secret)
    except Exception as e:
        logger.warning(f"Failed to get Stripe key from AWS Secrets Manager: {e}")
        # Fall back to environment variable for local development
        key = os.getenv("STRIPE_SECRET_KEY")
        if not key:
            raise ValueError(
                f"STRIPE_SECRET_KEY not available. Set STRIPE_SECRET_KEY env var or configure AWS Secrets Manager."
            )
        return key


def get_ssh_private_key() -> str:
    """
    Get SSH private key from AWS Secrets Manager.
    Falls back to environment variable for local development.
    
    Returns:
        str: The SSH private key content
    """
    secret_name = os.getenv("SSH_PRIVATE_KEY_SECRET_NAME", "chonky/dev/ssh_private_key")
    
    # Try AWS Secrets Manager first
    try:
        secret = get_secret(secret_name)
        if isinstance(secret, dict) and "private_key" in secret:
            return secret["private_key"]
        if isinstance(secret, dict) and "value" in secret:
            return secret["value"]
        if isinstance(secret, dict) and "ssh_private_key" in secret:
            return secret["ssh_private_key"]
        return str(secret)
    except Exception as e:
        logger.warning(f"Failed to get SSH key from AWS Secrets Manager: {e}")
        # Fall back to environment variable for local development
        key = os.getenv("SSH_PRIVATE_KEY")
        if not key:
            raise ValueError(
                f"SSH_PRIVATE_KEY not available. Set SSH_PRIVATE_KEY env var or configure AWS Secrets Manager."
            )
        return key
