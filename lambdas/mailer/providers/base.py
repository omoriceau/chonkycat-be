"""
email/providers/base.py

Abstract email provider interface.
Each method maps to one event type the Email Lambda handles.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class EmailAddress:
    address: str
    name:    Optional[str] = None

    def formatted(self) -> str:
        return f"{self.name} <{self.address}>" if self.name else self.address


# ---------------------------------------------------------------------------
# One dataclass per email type — mirrors the EventBridge event payloads
# but shaped for rendering (pre-formatted strings, display-ready fields)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrderConfirmationContext:
    to:               EmailAddress
    order_id:         int
    total_amount:     str
    currency:         str
    items:            list[dict]
    shipping_name:    str
    shipping_address: str
    promotion_code:   Optional[str]
    discount:         str
    subtotal:         str
    tax:              str
    shipping_fee:     str


@dataclass(frozen=True)
class OrderFailureContext:
    to:            EmailAddress
    order_id:      int
    error_message: str
    support_email: str


@dataclass(frozen=True)
class RefundConfirmationContext:
    to:        EmailAddress
    order_id:  int
    payment_id: int
    refund_id: str
    amount:    str
    currency:  str


@dataclass(frozen=True)
class LowStockAlertContext:
    to:       EmailAddress      # internal ops/admin recipient
    products: list[dict]        # [{sku, name, category, current_stock, threshold}]


@dataclass(frozen=True)
class WelcomeEmailContext:
    to:         EmailAddress
    first_name: Optional[str]


@dataclass(frozen=True)
class PasswordResetContext:
    to:         EmailAddress
    first_name: Optional[str]
    reset_link: str
    expires_in: int             # seconds


@dataclass(frozen=True)
class OrderSummaryContext:
    to:         EmailAddress
    first_name: Optional[str]
    orders:     list[dict]


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------

class EmailProvider(ABC):

    @abstractmethod
    def send_order_confirmation(self, ctx: OrderConfirmationContext) -> bool: ...

    @abstractmethod
    def send_order_failure(self, ctx: OrderFailureContext) -> bool: ...

    @abstractmethod
    def send_refund_confirmation(self, ctx: RefundConfirmationContext) -> bool: ...

    @abstractmethod
    def send_low_stock_alert(self, ctx: LowStockAlertContext) -> bool: ...

    @abstractmethod
    def send_welcome(self, ctx: WelcomeEmailContext) -> bool: ...

    @abstractmethod
    def send_password_reset(self, ctx: PasswordResetContext) -> bool: ...

    @abstractmethod
    def send_order_summary(self, ctx: OrderSummaryContext) -> bool: ...


class EmailProviderFactory(ABC):

    @abstractmethod
    def get_provider(self, name: str) -> EmailProvider: ...
