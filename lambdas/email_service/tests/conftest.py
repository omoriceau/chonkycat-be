"""
Shared pytest fixtures for the email_service Lambda test suite.

No AWS calls of any kind — every test mocks SES at the DefaultEmailProviderFactory
boundary (lambda_handler tests) or the boto3 SES client directly (ses_provider
tests), so nothing here needs moto.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# ses_provider.py reads these at *module import time*, so they must be set
# before anything imports it (directly or via lambda_handler/factory).
os.environ.setdefault("EMAIL_FROM_ADDRESS", "orders@chonkycat.test")
os.environ.setdefault("EMAIL_FROM_NAME", "ChonkyChonk Test")
os.environ.setdefault("SUPPORT_EMAIL", "support@chonkycat.test")
os.environ.setdefault("ENVIRONMENT", "dev")
