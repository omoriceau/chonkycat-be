"""
payments/providers/factory.py

Resolves a provider name to a concrete PaymentProvider.
Register new providers here — nothing else needs to change.
"""

from payments.providers.base import PaymentProvider, PaymentProviderFactory
from payments.providers.stripe_provider import StripeProvider


class DefaultPaymentProviderFactory(PaymentProviderFactory):
    """
    Registry of available payment providers.

    To add a new provider (e.g. PayPal):
        1. Create payments/providers/paypal_provider.py implementing PaymentProvider
        2. Import it here and add an entry to _REGISTRY
    """

    _REGISTRY: dict[str, type[PaymentProvider]] = {
        "stripe": StripeProvider,
        # "paypal":  PayPalProvider,
        # "adyen":   AdyenProvider,
    }

    def get_provider(self, name: str) -> PaymentProvider:
        key = name.strip().lower()
        cls = self._REGISTRY.get(key)
        if cls is None:
            available = ", ".join(self._REGISTRY.keys())
            raise ValueError(
                f"Unknown payment provider '{name}'. Available: {available}"
            )
        return cls()
