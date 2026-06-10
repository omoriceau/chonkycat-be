"""
payments/providers/base.py

Abstract base classes defining the payment provider contract.
Any payment provider must implement these interfaces.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PaymentStatus(str, Enum):
    PENDING   = "pending"
    PAID      = "paid"
    FAILED    = "failed"
    CANCELLED = "cancelled"
    REFUNDED  = "refunded"


class RefundStatus(str, Enum):
    PENDING   = "pending"
    REFUNDED  = "refunded"
    FAILED    = "failed"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PaymentRequest:
    order_id:       int
    amount:         Decimal          # in major currency units (e.g. 49.53 CAD)
    currency:       str              # ISO 4217 (e.g. "CAD")
    customer_email: str
    description:    str = ""
    metadata:       dict = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentResult:
    success:                bool
    provider:               str
    provider_transaction_id: str
    status:                 PaymentStatus
    amount:                 Decimal
    currency:               str
    error_code:             Optional[str]  = None
    error_message:          Optional[str]  = None
    raw_response:           Optional[dict] = None  # full provider payload for logging


@dataclass(frozen=True)
class RefundRequest:
    payment_id:              int       # internal payments.id
    provider_transaction_id: str       # provider's charge/payment-intent id
    amount:                  Decimal   # partial or full
    reason:                  str = ""


@dataclass(frozen=True)
class RefundResult:
    success:           bool
    provider:          str
    refund_id:         str
    status:            RefundStatus
    amount:            Decimal
    currency:          str
    error_code:        Optional[str]  = None
    error_message:     Optional[str]  = None
    raw_response:      Optional[dict] = None


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------

class PaymentProvider(ABC):
    """
    Contract every payment provider must satisfy.
    Implementations live in payments/providers/<name>.py
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier stored in payments.payment_provider."""
        ...

    @abstractmethod
    def charge(self, request: PaymentRequest) -> PaymentResult:
        """
        Initiate a charge.
        Must never raise — catch provider exceptions and return a failed PaymentResult.
        """
        ...

    @abstractmethod
    def refund(self, request: RefundRequest) -> RefundResult:
        """
        Issue a refund against a previous charge.
        Must never raise — catch provider exceptions and return a failed RefundResult.
        """
        ...


# ---------------------------------------------------------------------------
# Provider factory interface
# ---------------------------------------------------------------------------

class PaymentProviderFactory(ABC):
    """Resolves a provider name string to a concrete PaymentProvider instance."""

    @abstractmethod
    def get_provider(self, name: str) -> PaymentProvider:
        ...
