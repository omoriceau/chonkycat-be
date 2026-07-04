"""
orders/email/factory.py

Register email providers here. Same pattern as the payment provider factory.

To add SendGrid:
  1. Create orders/email/sendgrid_provider.py implementing EmailProvider
  2. Add it to _REGISTRY below
"""

from email_service.base import EmailProvider, EmailProviderFactory
from email_service.ses_provider import SESEmailProvider


class DefaultEmailProviderFactory(EmailProviderFactory):

    _REGISTRY: dict[str, type[EmailProvider]] = {
        "ses":      SESEmailProvider,
        # "sendgrid": SendGridEmailProvider,
        # "postmark": PostmarkEmailProvider,
    }

    def get_provider(self, name: str) -> EmailProvider:
        key = name.strip().lower()
        cls = self._REGISTRY.get(key)
        if cls is None:
            available = ", ".join(self._REGISTRY.keys())
            raise ValueError(f"Unknown email provider '{name}'. Available: {available}")
        return cls()
