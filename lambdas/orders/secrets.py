"""
Unified secrets retrieval from AWS Secrets Manager with fallback to environment variables.
"""

import json
import os
from functools import lru_cache
from typing import Optional

try:
    import boto3
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


@lru_cache(maxsize=10)
def get_secret(secret_name: str, default: Optional[str] = None) -> str:
    """
    Retrieve a secret from AWS Secrets Manager with fallback to environment variable.
    
    Args:
        secret_name: Name of the secret in Secrets Manager (e.g., 'chonky/dev/db_pass')
        default: Default value if secret is not found
    
    Returns:
        The secret value
    
    Raises:
        RuntimeError: If secret cannot be retrieved and no default is provided
    """
    # Try AWS Secrets Manager first
    if BOTO3_AVAILABLE:
        try:
            client = boto3.client('secretsmanager')
            response = client.get_secret_value(SecretId=secret_name)
            
            # Handle both string and JSON secrets
            if 'SecretString' in response:
                secret = response['SecretString']
                try:
                    # Try to parse as JSON
                    return json.loads(secret)
                except json.JSONDecodeError:
                    # Return as string if not JSON
                    return secret
            elif 'SecretBinary' in response:
                return response['SecretBinary']
        except Exception as e:
            print(f"[WARN] Failed to retrieve secret '{secret_name}' from AWS Secrets Manager: {str(e)}")
    
    # Fallback to environment variable
    env_var_name = secret_name.replace('/', '_').replace('-', '_').upper()
    env_value = os.environ.get(env_var_name)
    
    if env_value:
        print(f"[INFO] Using environment variable {env_var_name} as fallback for '{secret_name}'")
        return env_value
    
    # If we have a default, use it
    if default is not None:
        print(f"[INFO] Using default value for secret '{secret_name}'")
        return default
    
    # No secret found
    raise RuntimeError(
        f"Secret '{secret_name}' not found in AWS Secrets Manager and environment variable "
        f"'{env_var_name}' is not set. Set the environment variable or configure AWS Secrets Manager."
    )


def get_db_password() -> str:
    """
    Retrieve the database password from AWS Secrets Manager or environment variables.
    
    Checks for:
    1. AWS Secrets Manager secret named from DB_PASSWORD_SECRET_NAME env var
    2. Fallback to DB_PASSWORD environment variable
    
    Returns:
        The database password
    
    Raises:
        RuntimeError: If password cannot be retrieved
    """
    secret_name = os.environ.get('DB_PASSWORD_SECRET_NAME', 'chonky/dev/db_pass')
    try:
        return get_secret(secret_name)
    except RuntimeError:
        # Try direct env var as final fallback
        db_password = os.environ.get('DB_PASSWORD')
        if db_password:
            print(f"[INFO] Using DB_PASSWORD environment variable as fallback")
            return db_password
        raise RuntimeError(
            "Database password not found. Set DB_PASSWORD_SECRET_NAME to point to AWS Secrets Manager, "
            "or set DB_PASSWORD environment variable."
        )


def get_stripe_key() -> str:
    """
    Retrieve the Stripe secret key from AWS Secrets Manager or environment variables.
    
    Checks for:
    1. AWS Secrets Manager secret named from STRIPE_SECRET_KEY_SECRET_NAME env var
    2. Fallback to STRIPE_SECRET_KEY environment variable
    
    Returns:
        The Stripe secret key
    
    Raises:
        RuntimeError: If key cannot be retrieved
    """
    secret_name = os.environ.get('STRIPE_SECRET_KEY_SECRET_NAME', 'chonky/dev/stripe_secret_key')
    try:
        return get_secret(secret_name)
    except RuntimeError:
        # Try direct env var as final fallback
        stripe_key = os.environ.get('STRIPE_SECRET_KEY')
        if stripe_key:
            print(f"[INFO] Using STRIPE_SECRET_KEY environment variable as fallback")
            return stripe_key
        raise RuntimeError(
            "Stripe secret key not found. Set STRIPE_SECRET_KEY_SECRET_NAME to point to AWS Secrets Manager, "
            "or set STRIPE_SECRET_KEY environment variable."
        )


def get_ssh_private_key() -> str:
    """
    Retrieve the SSH private key from AWS Secrets Manager or environment variables.
    
    Checks for:
    1. AWS Secrets Manager secret named from SSH_PRIVATE_KEY_SECRET_NAME env var
    2. Fallback to SSH_PRIVATE_KEY environment variable
    
    Returns:
        The SSH private key
    
    Raises:
        RuntimeError: If key cannot be retrieved
    """
    secret_name = os.environ.get('SSH_PRIVATE_KEY_SECRET_NAME', 'chonky/dev/ssh_private_key')
    try:
        return get_secret(secret_name)
    except RuntimeError:
        # Try direct env var as final fallback
        ssh_key = os.environ.get('SSH_PRIVATE_KEY')
        if ssh_key:
            print(f"[INFO] Using SSH_PRIVATE_KEY environment variable as fallback")
            return ssh_key
        raise RuntimeError(
            "SSH private key not found. Set SSH_PRIVATE_KEY_SECRET_NAME to point to AWS Secrets Manager, "
            "or set SSH_PRIVATE_KEY environment variable."
        )
