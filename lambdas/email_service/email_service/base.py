"""
orders/email/base.py

Abstract email interface + value objects.
Swap SES for SendGrid, Postmark, etc. without touching anything else.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EmailAddress:
    address: str
    name:    Optional[str] = None

    def formatted(self) -> str:
        return f"{self.name} <{self.address}>" if self.name else self.address


@dataclass(frozen=True)
class OrderConfirmationEmail:
    to:               EmailAddress
    order_id:         int
    total_amount:     str
    currency:         str
    items:            list[dict]      # [{name, quantity, unit_price, line_total}]
    shipping_name:    str
    shipping_address: str             # formatted single string
    promotion_code:   Optional[str]
    discount:         str
    subtotal:         str
    tax:              str
    shipping_fee:     str


@dataclass(frozen=True)
class OrderFailureEmail:
    to:           EmailAddress
    order_id:     int
    error_message: str
    support_email: str


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class EmailProvider(ABC):

    @abstractmethod
    def send_order_confirmation(self, email: OrderConfirmationEmail) -> bool:
        """Returns True on success. Must never raise."""
        ...

    @abstractmethod
    def send_order_failure(self, email: OrderFailureEmail) -> bool:
        """Returns True on success. Must never raise."""
        ...


# ---------------------------------------------------------------------------
# Factory interface
# ---------------------------------------------------------------------------

class EmailProviderFactory(ABC):

    @abstractmethod
    def get_provider(self, name: str) -> EmailProvider:
        ...
