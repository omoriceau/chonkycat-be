"""
shared/events.py

Canonical EventBridge event definitions for the ChonkyChonk platform.

Every Lambda that emits or consumes events imports from here so the
event shape is defined in exactly one place.

Bus name:    chonkychonk-bus
SNS topic:   chonkychonk-ops-alerts  (payment unreachable / infra alerts)
"""

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Event source + detail-type constants
# Keep these in sync with your EventBridge rules.
# ---------------------------------------------------------------------------

SOURCE_ORDERS   = "chonkychonk.orders"
SOURCE_PAYMENTS = "chonkychonk.payments"
SOURCE_USERS    = "chonkychonk.users"
SOURCE_INVENTORY= "chonkychonk.inventory"

# detail-type values
ORDER_CREATED          = "OrderCreated"
PAYMENT_SETTLED        = "PaymentSettled"
PAYMENT_FAILED         = "PaymentFailed"
REFUND_COMPLETE        = "RefundComplete"
LOW_STOCK_DETECTED     = "LowStockDetected"
USER_REGISTERED        = "UserRegistered"
PASSWORD_RESET_REQUEST = "PasswordResetRequest"
ORDER_SUMMARY_REQUEST  = "OrderSummaryRequest"

# SNS — ops alerts (not EventBridge)
SNS_PAYMENT_UNREACHABLE = "PaymentUnreachable"


# ---------------------------------------------------------------------------
# Event payload dataclasses
# These are what goes into EventBridge detail / SNS Message.
# ---------------------------------------------------------------------------

@dataclass
class OrderCreatedEvent:
    """Emitted by Order Lambda. Consumed by Payment Lambda."""
    event_type:       str        # "charge"
    order_id:         int
    amount:           str
    currency:         str
    customer_email:   str
    provider:         str
    description:      str
    connection_id:    Optional[str]
    subtotal:         str
    discount:         str
    tax:              str
    shipping_fee:     str
    promotion_code:   Optional[str]
    shipping_name:    str
    shipping_address: str
    items:            list[dict]  # [{name, quantity, unit_price, line_total}]


@dataclass
class PaymentSettledEvent:
    """Emitted by Payment Lambda on successful charge. Consumed by Email Lambda."""
    order_id:                 int
    customer_email:           str
    customer_name:            Optional[str]
    amount:                   str
    currency:                 str
    provider_transaction_id:  str
    subtotal:                 str
    discount:                 str
    tax:                      str
    shipping_fee:             str
    promotion_code:           Optional[str]
    shipping_name:            str
    shipping_address:         str
    items:                    list[dict]


@dataclass
class PaymentFailedEvent:
    """Emitted by Payment Lambda on failed charge. Consumed by Email Lambda."""
    order_id:       int
    customer_email: str
    customer_name:  Optional[str]
    error_message:  str
    error_code:     Optional[str]


@dataclass
class RefundCompleteEvent:
    """Emitted by Payment Lambda on successful refund. Consumed by Email Lambda."""
    order_id:       int
    payment_id:     int
    customer_email: str
    customer_name:  Optional[str]
    refund_id:      str
    amount:         str
    currency:       str


@dataclass
class LowStockDetectedEvent:
    """Emitted by Order Lambda after stock is decremented. Consumed by Email Lambda."""
    products: list[dict]   # [{product_id, sku, name, category, current_stock, threshold}]


@dataclass
class UserRegisteredEvent:
    """Emitted by User Lambda (or Cognito trigger) on new registration. Consumed by Email Lambda."""
    user_id:    int
    email:      str
    first_name: Optional[str]
    last_name:  Optional[str]


@dataclass
class PasswordResetRequestEvent:
    """
    Emitted when a user requests a password reset.
    Stub — fully wired once Cognito is configured.
    Cognito can trigger this directly via a Lambda trigger on ForgotPassword.
    """
    email:      str
    reset_link: str           # Cognito will provide this
    expires_in: int = 3600    # seconds


@dataclass
class OrderSummaryRequestEvent:
    """Emitted when a user requests their order history by email."""
    user_id:        int
    email:          str
    first_name:     Optional[str]
    orders:         list[dict]  # [{order_id, status, total, created_at, items[]}]
