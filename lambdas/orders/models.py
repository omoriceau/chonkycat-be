"""
orders/models.py

Typed request/response models for the orders Lambda.
Validated on entry before any DB or event interaction.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    pass


# ---------------------------------------------------------------------------
# Inbound — what the frontend POSTs
# ---------------------------------------------------------------------------

@dataclass
class OrderItemRequest:
    product_id: str
    quantity:   int


@dataclass
class ShippingAddress:
    name:        str
    address1:    str
    city:        str
    province:    str
    postal_code: str
    country:     str
    address2:    Optional[str] = None


@dataclass
class CreateOrderRequest:
    user_id:          str
    items:            list[OrderItemRequest]
    shipping:         ShippingAddress
    currency:         str            = "CAD"
    promotion_code:   Optional[str]  = None
    customer_notes:   Optional[str]  = None
    customer_email:   str            = ""
    connection_id:    Optional[str]  = None   # API GW WebSocket connection id
    payment_provider: str            = "stripe"


# ---------------------------------------------------------------------------
# Internal — resolved after DB product lookup
# ---------------------------------------------------------------------------

@dataclass
class ResolvedOrderItem:
    product_id:     str
    name_snapshot:  str
    quantity:       int
    unit_price:     Decimal
    line_total:     Decimal


@dataclass
class ResolvedOrder:
    user_id:          str
    customer_email:   str
    items:            list[ResolvedOrderItem]
    shipping:         ShippingAddress
    subtotal:         Decimal
    tax_amount:       Decimal
    shipping_amount:  Decimal
    discount_amount:  Decimal
    total_amount:     Decimal
    # NOTE: the promotions table's key is the code itself (no surrogate id in
    # the new schema), so this now holds the normalized code string, not an int.
    promotion_id:     Optional[str]
    promotion_code:   Optional[str]
    customer_notes:   Optional[str]
    currency:         str
    connection_id:    Optional[str]
    payment_provider: str


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _require(d: dict, key: str) -> any:
    val = d.get(key)
    if val is None:
        raise ValidationError(f"Missing required field: '{key}'")
    return val


def parse_create_order_request(data: dict) -> CreateOrderRequest:
    # Items
    raw_items = _require(data, "items")
    if not isinstance(raw_items, list) or len(raw_items) == 0:
        raise ValidationError("'items' must be a non-empty list")

    items = []
    for i, item in enumerate(raw_items):
        try:
            product_id = str(_require(item, "product_id"))
            quantity   = int(_require(item, "quantity"))
        except (TypeError, ValueError) as e:
            raise ValidationError(f"items[{i}]: {e}")
        if quantity < 1:
            raise ValidationError(f"items[{i}]: quantity must be >= 1")
        items.append(OrderItemRequest(product_id=product_id, quantity=quantity))

    # Shipping
    raw_shipping = _require(data, "shipping")
    shipping = ShippingAddress(
        name        = str(_require(raw_shipping, "name")),
        address1    = str(_require(raw_shipping, "address1")),
        city        = str(_require(raw_shipping, "city")),
        province    = str(_require(raw_shipping, "province")),
        postal_code = str(_require(raw_shipping, "postal_code")),
        country     = str(_require(raw_shipping, "country")),
        address2    = raw_shipping.get("address2"),
    )

    return CreateOrderRequest(
        user_id          = str(_require(data, "user_id")),
        customer_email   = str(_require(data, "customer_email")),
        items            = items,
        shipping         = shipping,
        currency         = str(data.get("currency", "CAD")).upper(),
        promotion_code   = data.get("promotion_code"),
        customer_notes   = data.get("customer_notes"),
        connection_id    = data.get("connection_id"),
        payment_provider = str(data.get("payment_provider", "stripe")),
    )


def parse_update_order_request(data: dict) -> dict:
    """
    Parse optional fields for order updates.
    Allows partial updates: items, shipping, customer_notes, or any combination.
    Returns a dict with only the provided fields.
    """
    update = {}
    
    # Items (optional)
    if "items" in data:
        raw_items = data["items"]
        if not isinstance(raw_items, list) or len(raw_items) == 0:
            raise ValidationError("'items' must be a non-empty list")
        
        items = []
        for i, item in enumerate(raw_items):
            try:
                product_id = str(_require(item, "product_id"))
                quantity   = int(_require(item, "quantity"))
            except (TypeError, ValueError) as e:
                raise ValidationError(f"items[{i}]: {e}")
            if quantity < 1:
                raise ValidationError(f"items[{i}]: quantity must be >= 1")
            items.append(OrderItemRequest(product_id=product_id, quantity=quantity))
        update["items"] = items
    
    # Shipping (optional)
    if "shipping" in data:
        raw_shipping = data["shipping"]
        shipping = ShippingAddress(
            name        = str(_require(raw_shipping, "name")),
            address1    = str(_require(raw_shipping, "address1")),
            city        = str(_require(raw_shipping, "city")),
            province    = str(_require(raw_shipping, "province")),
            postal_code = str(_require(raw_shipping, "postal_code")),
            country     = str(_require(raw_shipping, "country")),
            address2    = raw_shipping.get("address2"),
        )
        update["shipping"] = shipping
    
    # Customer notes (optional)
    if "customer_notes" in data:
        update["customer_notes"] = data.get("customer_notes")
    
    # Promotion code (optional)
    if "promotion_code" in data:
        update["promotion_code"] = data.get("promotion_code")
    
    if not update:
        raise ValidationError("At least one field must be provided for update (items, shipping, customer_notes, or promotion_code)")
    
    return update
