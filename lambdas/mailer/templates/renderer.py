"""
email/templates/renderer.py

Pure functions that return (subject, html, text) tuples.
No provider logic here — just string rendering.
Keeping templates separate means you can unit-test them without SES.
"""

from textwrap import dedent
from email.providers.base import (
    OrderConfirmationContext,
    OrderFailureContext,
    RefundConfirmationContext,
    LowStockAlertContext,
    WelcomeEmailContext,
    PasswordResetContext,
    OrderSummaryContext,
)

SUPPORT_EMAIL = "support@chonkychonk.com"  # overridden by env var in ses_provider


# ---------------------------------------------------------------------------
# Shared HTML chrome
# ---------------------------------------------------------------------------

def _header(title: str, subtitle: str, colour: str = "#c97d3e") -> str:
    return f"""
    <tr><td style="background:{colour};padding:32px;text-align:center">
      <h1 style="margin:0;color:#fff;font-size:28px;letter-spacing:1px">ChonkyChonk 🐾</h1>
      <p style="margin:8px 0 0;color:rgba(255,255,255,.8);font-size:15px">{subtitle}</p>
    </td></tr>"""


def _footer() -> str:
    return """
    <tr><td style="background:#fdf7f0;padding:20px;text-align:center;font-size:12px;color:#aaa">
      © ChonkyChonk — premium fuel for the discerning chonk
    </td></tr>"""


def _wrap(header: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#fdf7f0;font-family:Georgia,serif;color:#3a2e24">
  <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
    <table width="600" cellpadding="0" cellspacing="0"
           style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">
      {header}
      <tr><td style="padding:32px">{body}</td></tr>
      {_footer()}
    </table>
  </td></tr></table>
</body></html>"""


def _item_rows(items: list[dict], currency: str) -> str:
    return "\n".join(
        f"""<tr>
              <td style="padding:6px 0;border-bottom:1px solid #f0e6d3">{i['name']}</td>
              <td style="padding:6px 0;border-bottom:1px solid #f0e6d3;text-align:center">{i['quantity']}</td>
              <td style="padding:6px 0;border-bottom:1px solid #f0e6d3;text-align:right">${i['line_total']} {currency}</td>
            </tr>"""
        for i in items
    )


# ---------------------------------------------------------------------------
# 1. Order confirmation
# ---------------------------------------------------------------------------

def render_order_confirmation(ctx: OrderConfirmationContext) -> tuple[str, str, str]:
    subject = f"Your ChonkyChonk order #{ctx.order_id} is confirmed! 🐾"

    promo_row = (
        f'<tr><td colspan="2" style="padding:4px 0;color:#888">Promo ({ctx.promotion_code})</td>'
        f'<td style="padding:4px 0;text-align:right;color:#5a8a5a">-${ctx.discount} {ctx.currency}</td></tr>'
        if ctx.promotion_code else ""
    )

    body = f"""
      <p style="margin:0 0 24px;font-size:16px">
        Hi {ctx.to.name or "there"}, your chonky treats are headed to
        <strong>{ctx.shipping_name}</strong> at {ctx.shipping_address}.
      </p>
      <table width="100%" cellpadding="0" cellspacing="0" style="font-size:14px;margin-bottom:24px">
        <tr style="border-bottom:2px solid #c97d3e">
          <th style="padding:8px 0;text-align:left;color:#c97d3e">Item</th>
          <th style="padding:8px 0;text-align:center;color:#c97d3e">Qty</th>
          <th style="padding:8px 0;text-align:right;color:#c97d3e">Total</th>
        </tr>
        {_item_rows(ctx.items, ctx.currency)}
        {promo_row}
        <tr><td colspan="2" style="padding:6px 0;color:#888">Subtotal</td>
            <td style="padding:6px 0;text-align:right">${ctx.subtotal} {ctx.currency}</td></tr>
        <tr><td colspan="2" style="padding:6px 0;color:#888">Tax</td>
            <td style="padding:6px 0;text-align:right">${ctx.tax} {ctx.currency}</td></tr>
        <tr><td colspan="2" style="padding:6px 0;color:#888">Shipping</td>
            <td style="padding:6px 0;text-align:right">${ctx.shipping_fee} {ctx.currency}</td></tr>
        <tr style="border-top:2px solid #c97d3e;font-weight:bold;font-size:16px">
          <td colspan="2" style="padding:10px 0">Total charged</td>
          <td style="padding:10px 0;text-align:right;color:#c97d3e">${ctx.total_amount} {ctx.currency}</td>
        </tr>
      </table>
      <p style="margin:0;font-size:13px;color:#888">
        Questions? <a href="mailto:{SUPPORT_EMAIL}" style="color:#c97d3e">{SUPPORT_EMAIL}</a>
      </p>"""

    html = _wrap(_header("Order Confirmed", "Your order is confirmed!"), body)

    text = dedent(f"""\
        ChonkyChonk — Order #{ctx.order_id} Confirmed

        Hi {ctx.to.name or "there"},

        Shipping to: {ctx.shipping_name}, {ctx.shipping_address}

        Items:
        """ + "\n".join(f"  {i['name']} x{i['quantity']} — ${i['line_total']} {ctx.currency}" for i in ctx.items) + f"""

        {"Promo (" + ctx.promotion_code + "): -$" + ctx.discount + " " + ctx.currency if ctx.promotion_code else ""}
        Subtotal:  ${ctx.subtotal} {ctx.currency}
        Tax:       ${ctx.tax} {ctx.currency}
        Shipping:  ${ctx.shipping_fee} {ctx.currency}
        Total:     ${ctx.total_amount} {ctx.currency}

        Questions? {SUPPORT_EMAIL}
    """)

    return subject, html, text


# ---------------------------------------------------------------------------
# 2. Order failure
# ---------------------------------------------------------------------------

def render_order_failure(ctx: OrderFailureContext) -> tuple[str, str, str]:
    subject = f"There was a problem with your ChonkyChonk order #{ctx.order_id}"

    body = f"""
      <p style="margin:0 0 16px;font-size:16px">
        Hi {ctx.to.name or "there"}, we were unable to process payment for order
        <strong>#{ctx.order_id}</strong>.
      </p>
      <p style="margin:0 0 16px;font-size:14px;padding:12px;background:#fff5f5;border-radius:6px;color:#b84c4c">
        {ctx.error_message}
      </p>
      <p style="margin:0;font-size:14px">
        No charge has been made. Please try again or contact
        <a href="mailto:{ctx.support_email}" style="color:#b84c4c">{ctx.support_email}</a>.
      </p>"""

    html = _wrap(_header("Payment Issue", "There was a problem with your order", "#b84c4c"), body)

    text = dedent(f"""\
        ChonkyChonk — Problem with Order #{ctx.order_id}

        Hi {ctx.to.name or "there"},

        We could not process payment for order #{ctx.order_id}.
        Reason: {ctx.error_message}

        No charge was made. Contact {ctx.support_email} for help.
    """)

    return subject, html, text


# ---------------------------------------------------------------------------
# 3. Refund confirmation
# ---------------------------------------------------------------------------

def render_refund_confirmation(ctx: RefundConfirmationContext) -> tuple[str, str, str]:
    subject = f"Your ChonkyChonk refund of ${ctx.amount} {ctx.currency} is on its way"

    body = f"""
      <p style="margin:0 0 16px;font-size:16px">
        Hi {ctx.to.name or "there"}, your refund for order <strong>#{ctx.order_id}</strong>
        has been processed.
      </p>
      <table width="100%" cellpadding="0" cellspacing="0" style="font-size:14px;margin-bottom:24px">
        <tr><td style="padding:6px 0;color:#888">Refund amount</td>
            <td style="text-align:right;font-weight:bold;color:#5a8a5a">${ctx.amount} {ctx.currency}</td></tr>
        <tr><td style="padding:6px 0;color:#888">Refund ID</td>
            <td style="text-align:right;font-size:12px;color:#aaa">{ctx.refund_id}</td></tr>
      </table>
      <p style="margin:0;font-size:13px;color:#888">
        Funds typically appear within 5–10 business days depending on your bank.
        Questions? <a href="mailto:{SUPPORT_EMAIL}" style="color:#c97d3e">{SUPPORT_EMAIL}</a>
      </p>"""

    html = _wrap(_header("Refund Processed", f"${ctx.amount} {ctx.currency} is on its way back to you", "#5a8a5a"), body)

    text = dedent(f"""\
        ChonkyChonk — Refund for Order #{ctx.order_id}

        Hi {ctx.to.name or "there"},

        Your refund of ${ctx.amount} {ctx.currency} has been processed.
        Refund ID: {ctx.refund_id}

        Funds appear within 5-10 business days.
        Questions? {SUPPORT_EMAIL}
    """)

    return subject, html, text


# ---------------------------------------------------------------------------
# 4. Low stock alert (internal)
# ---------------------------------------------------------------------------

def render_low_stock_alert(ctx: LowStockAlertContext) -> tuple[str, str, str]:
    subject = f"⚠️ Low stock alert — {len(ctx.products)} product(s) need attention"

    rows = "\n".join(
        f"""<tr>
              <td style="padding:8px;border-bottom:1px solid #f0e6d3">{p['sku']}</td>
              <td style="padding:8px;border-bottom:1px solid #f0e6d3">{p['name']}</td>
              <td style="padding:8px;border-bottom:1px solid #f0e6d3;text-align:center">{p['category']}</td>
              <td style="padding:8px;border-bottom:1px solid #f0e6d3;text-align:center;color:#b84c4c;font-weight:bold">{p['current_stock']}</td>
              <td style="padding:8px;border-bottom:1px solid #f0e6d3;text-align:center;color:#888">{p['threshold']}</td>
            </tr>"""
        for p in ctx.products
    )

    body = f"""
      <p style="margin:0 0 20px;font-size:16px">
        The following products have dropped to or below their low stock threshold:
      </p>
      <table width="100%" cellpadding="0" cellspacing="0" style="font-size:13px">
        <tr style="background:#fdf7f0">
          <th style="padding:8px;text-align:left;color:#c97d3e">SKU</th>
          <th style="padding:8px;text-align:left;color:#c97d3e">Product</th>
          <th style="padding:8px;text-align:center;color:#c97d3e">Category</th>
          <th style="padding:8px;text-align:center;color:#c97d3e">In Stock</th>
          <th style="padding:8px;text-align:center;color:#c97d3e">Threshold</th>
        </tr>
        {rows}
      </table>"""

    html = _wrap(_header("Low Stock Alert", "Products need restocking", "#c97d3e"), body)

    text_rows = "\n".join(
        f"  [{p['sku']}] {p['name']} — {p['current_stock']} left (threshold: {p['threshold']})"
        for p in ctx.products
    )
    text = dedent(f"""\
        ChonkyChonk — Low Stock Alert

        The following products need restocking:

        {text_rows}
    """)

    return subject, html, text


# ---------------------------------------------------------------------------
# 5. Welcome email
# ---------------------------------------------------------------------------

def render_welcome(ctx: WelcomeEmailContext) -> tuple[str, str, str]:
    subject = "Welcome to ChonkyChonk 🐾 — your cat's new favourite place"
    name    = ctx.first_name or "there"

    body = f"""
      <p style="margin:0 0 16px;font-size:18px;font-weight:bold">Welcome, {name}!</p>
      <p style="margin:0 0 16px;font-size:15px;line-height:1.6">
        Your account is ready. Your cat has been notified and is already
        judging your purchasing decisions from across the room.
      </p>
      <p style="margin:0 0 24px;font-size:15px;line-height:1.6">
        Browse our range of artisanal kibble, gourmet pâtés, and ethically-sourced
        small-animal delicacies — all crafted for the discerning chonk.
      </p>
      <p style="text-align:center;margin:0 0 24px">
        <a href="https://chonkychonk.com/shop"
           style="background:#c97d3e;color:#fff;padding:14px 32px;border-radius:6px;
                  text-decoration:none;font-size:15px;display:inline-block">
          Start Shopping
        </a>
      </p>
      <p style="margin:0;font-size:13px;color:#888">
        Questions? <a href="mailto:{SUPPORT_EMAIL}" style="color:#c97d3e">{SUPPORT_EMAIL}</a>
      </p>"""

    html = _wrap(_header("Welcome!", "Your cat's new favourite store"), body)

    text = dedent(f"""\
        Welcome to ChonkyChonk, {name}!

        Your account is ready. Browse our range at https://chonkychonk.com/shop

        Questions? {SUPPORT_EMAIL}
    """)

    return subject, html, text


# ---------------------------------------------------------------------------
# 6. Password reset (stub — link provided by Cognito)
# ---------------------------------------------------------------------------

def render_password_reset(ctx: PasswordResetContext) -> tuple[str, str, str]:
    subject = "Reset your ChonkyChonk password"
    name    = ctx.first_name or "there"
    expires = ctx.expires_in // 60  # minutes

    body = f"""
      <p style="margin:0 0 16px;font-size:16px">Hi {name},</p>
      <p style="margin:0 0 24px;font-size:15px;line-height:1.6">
        We received a request to reset your password. Click below to choose a new one.
        This link expires in <strong>{expires} minutes</strong>.
      </p>
      <p style="text-align:center;margin:0 0 24px">
        <a href="{ctx.reset_link}"
           style="background:#c97d3e;color:#fff;padding:14px 32px;border-radius:6px;
                  text-decoration:none;font-size:15px;display:inline-block">
          Reset Password
        </a>
      </p>
      <p style="margin:0;font-size:13px;color:#888">
        If you didn't request this, you can safely ignore this email.
        Your password won't change until you click the link above.
      </p>"""

    html = _wrap(_header("Password Reset", "Click below to reset your password"), body)

    text = dedent(f"""\
        ChonkyChonk — Password Reset

        Hi {name},

        Reset your password here (expires in {expires} minutes):
        {ctx.reset_link}

        If you didn't request this, ignore this email.
    """)

    return subject, html, text


# ---------------------------------------------------------------------------
# 7. Order summary / account history
# ---------------------------------------------------------------------------

def render_order_summary(ctx: OrderSummaryContext) -> tuple[str, str, str]:
    subject = "Your ChonkyChonk order history"
    name    = ctx.first_name or "there"

    if not ctx.orders:
        order_html = '<p style="color:#888;font-size:14px">No orders yet — your cat is waiting.</p>'
        order_text = "  No orders yet."
    else:
        order_blocks = []
        order_texts  = []
        for o in ctx.orders:
            item_rows = "".join(
                f'<div style="font-size:13px;color:#888;padding:2px 0">'
                f'  {i["name"]} x{i["quantity"]}</div>'
                for i in o.get("items", [])
            )
            order_blocks.append(f"""
              <div style="border:1px solid #f0e6d3;border-radius:8px;padding:16px;margin-bottom:16px">
                <div style="display:flex;justify-content:space-between;margin-bottom:8px">
                  <strong>Order #{o['order_id']}</strong>
                  <span style="color:#c97d3e">${o['total']} {o.get('currency','CAD')}</span>
                </div>
                <div style="font-size:13px;color:#888;margin-bottom:8px">
                  {o['created_at'][:10]} &nbsp;·&nbsp;
                  <span style="text-transform:capitalize">{o['status']}</span>
                </div>
                {item_rows}
              </div>""")
            order_texts.append(
                f"  Order #{o['order_id']} — ${o['total']} — {o['status']} — {o['created_at'][:10]}"
            )
        order_html = "\n".join(order_blocks)
        order_text = "\n".join(order_texts)

    body = f"""
      <p style="margin:0 0 24px;font-size:16px">Hi {name}, here's your order history:</p>
      {order_html}
      <p style="margin:16px 0 0;font-size:13px;color:#888">
        Questions? <a href="mailto:{SUPPORT_EMAIL}" style="color:#c97d3e">{SUPPORT_EMAIL}</a>
      </p>"""

    html = _wrap(_header("Order History", "Your ChonkyChonk orders"), body)

    text = dedent(f"""\
        ChonkyChonk — Order History for {name}

        {order_text}

        Questions? {SUPPORT_EMAIL}
    """)

    return subject, html, text
