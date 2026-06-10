"""
email/providers/factory.py

Add new email providers here — nothing else changes.
"""

from email.providers.base import EmailProvider, EmailProviderFactory
from email.providers.ses_provider import SESEmailProvider


class DefaultEmailProviderFactory(EmailProviderFactory):

    _REGISTRY: dict[str, type[EmailProvider]] = {
        "ses": SESEmailProvider,
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
