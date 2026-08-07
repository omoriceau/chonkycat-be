"""
orders/email/ses_provider.py

AWS SES implementation of EmailProvider.

Environment Variables:
  - EMAIL_FROM_ADDRESS    Verified SES sender address (e.g. orders@chonkychonk.com)
  - EMAIL_FROM_NAME       Display name (default: ChonkyChonk)
  - SUPPORT_EMAIL         Shown in failure emails (default: same as FROM)
  - SES_REGION            AWS region for SES (default: inherits Lambda region)
  - SES_CONFIGURATION_SET (optional) SES configuration set name for tracking
"""

import logging
import os
from html import escape
from textwrap import dedent

import boto3
from botocore.exceptions import ClientError

from email_service.base import (
    EmailProvider,
    OrderConfirmationEmail,
    OrderFailureEmail,
    WelcomeEmail,
)

logger = logging.getLogger(__name__)

FROM_ADDRESS       = os.environ.get("EMAIL_FROM_ADDRESS", "orders@chonkychonk.com")
FROM_NAME          = os.environ.get("EMAIL_FROM_NAME",    "ChonkyChonk")
SUPPORT_EMAIL      = os.environ.get("SUPPORT_EMAIL",      FROM_ADDRESS)
CONFIGURATION_SET  = os.environ.get("SES_CONFIGURATION_SET")


def _short_order_id(order_id) -> str:
    """
    order_id is a full UUID (see orders/service.py) — customers never need
    the whole thing, just enough to reference the order in a support
    conversation. Matches the profile page's order history display
    (Profile.jsx: order.order_id.slice(0, 8)), so the id a customer sees in
    their inbox is the same one they see on the site.
    """
    return str(order_id)[:8]


class SESEmailProvider(EmailProvider):

    def __init__(self, ses_client=None):
        self._ses = ses_client or boto3.client("ses")

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def send_order_confirmation(self, email: OrderConfirmationEmail, subject_prefix: str = "") -> bool:
        subject  = subject_prefix + f"Your ChonkyChonk order #{_short_order_id(email.order_id)} is confirmed! 🐾"
        html     = self._render_confirmation_html(email)
        text     = self._render_confirmation_text(email)
        return self._send(email.to.formatted(), subject, html, text)

    def send_order_failure(self, email: OrderFailureEmail, subject_prefix: str = "") -> bool:
        subject  = subject_prefix + f"There was a problem with your ChonkyChonk order #{_short_order_id(email.order_id)}"
        html     = self._render_failure_html(email)
        text     = self._render_failure_text(email)
        return self._send(email.to.formatted(), subject, html, text)

    def send_welcome_email(self, email: WelcomeEmail, subject_prefix: str = "") -> bool:
        subject  = subject_prefix + "Welcome to ChonkyChonk! 🐾"
        html     = self._render_welcome_html(email)
        text     = self._render_welcome_text(email)
        return self._send(email.to.formatted(), subject, html, text)

    # ------------------------------------------------------------------
    # SES send
    # ------------------------------------------------------------------

    def _send(self, to: str, subject: str, html: str, text: str) -> bool:
        kwargs = dict(
            Source=f"{FROM_NAME} <{FROM_ADDRESS}>",
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text, "Charset": "UTF-8"},
                    "Html": {"Data": html, "Charset": "UTF-8"},
                },
            },
        )
        if CONFIGURATION_SET:
            kwargs["ConfigurationSetName"] = CONFIGURATION_SET

        try:
            resp = self._ses.send_email(**kwargs)
            logger.info("SES email sent | MessageId=%s to=%s", resp["MessageId"], to)
            return True
        except ClientError as e:
            logger.error("SES send failed | to=%s error=%s", to, e)
            return False

    # ------------------------------------------------------------------
    # HTML templates
    # ------------------------------------------------------------------

    def _render_confirmation_html(self, e: OrderConfirmationEmail) -> str:
        rows = "\n".join(
            f"""<tr>
                  <td style="padding:6px 0;border-bottom:1px solid #f0e6d3">{item['name']}</td>
                  <td style="padding:6px 0;border-bottom:1px solid #f0e6d3;text-align:center">{item['quantity']}</td>
                  <td style="padding:6px 0;border-bottom:1px solid #f0e6d3;text-align:right">${item['line_total']} {e.currency}</td>
               </tr>"""
            for item in e.items
        )
        promo_row = (
            f'<tr><td colspan="2" style="padding:4px 0;color:#888">Promo ({e.promotion_code})</td>'
            f'<td style="padding:4px 0;text-align:right;color:#5a8a5a">-${e.discount} {e.currency}</td></tr>'
            if e.promotion_code else ""
        )
        notes_block = (
            f"""<p style="margin:0 0 24px;font-size:14px;color:#666">
                  <strong style="color:#3a2e24">Delivery instructions:</strong> {escape(e.customer_notes)}
                </p>"""
            if e.customer_notes else ""
        )

        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
        <body style="margin:0;padding:0;background:#fdf7f0;font-family:Georgia,serif;color:#3a2e24">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr><td align="center" style="padding:32px 16px">
              <table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">

                <!-- Header -->
                <tr><td style="background:#c97d3e;padding:32px;text-align:center">
                  <h1 style="margin:0;color:#fff;font-size:28px;letter-spacing:1px">ChonkyChonk 🐾</h1>
                  <p style="margin:8px 0 0;color:#ffe8cc;font-size:15px">Your order is confirmed!</p>
                </td></tr>

                <!-- Body -->
                <tr><td style="padding:32px">
                  <p style="margin:0 0 24px;font-size:16px">
                    Hi {e.to.name or "there"}, your chonky treats are on their way to <strong>{e.shipping_name}</strong>
                    at {e.shipping_address}.
                  </p>

                  <!-- Order items -->
                  <table width="100%" cellpadding="0" cellspacing="0" style="font-size:14px;margin-bottom:24px">
                    <tr style="border-bottom:2px solid #c97d3e">
                      <th style="padding:8px 0;text-align:left;color:#c97d3e">Item</th>
                      <th style="padding:8px 0;text-align:center;color:#c97d3e">Qty</th>
                      <th style="padding:8px 0;text-align:right;color:#c97d3e">Total</th>
                    </tr>
                    {rows}
                    {promo_row}
                  </table>

                  {notes_block}

                  <table width="100%" cellpadding="0" cellspacing="0" style="font-size:14px;margin-bottom:24px">
                    <tr><td colspan="2" style="padding:6px 0;color:#888">Subtotal</td>
                        <td style="padding:6px 0;text-align:right">${e.subtotal} {e.currency}</td></tr>
                    <tr><td colspan="2" style="padding:6px 0;color:#888">Tax</td>
                        <td style="padding:6px 0;text-align:right">${e.tax} {e.currency}</td></tr>
                    <tr><td colspan="2" style="padding:6px 0;color:#888">Shipping</td>
                        <td style="padding:6px 0;text-align:right">${e.shipping_fee} {e.currency}</td></tr>
                    <tr style="border-top:2px solid #c97d3e;font-weight:bold;font-size:16px">
                      <td colspan="2" style="padding:10px 0">Total charged</td>
                      <td style="padding:10px 0;text-align:right;color:#c97d3e">${e.total_amount} {e.currency}</td>
                    </tr>
                  </table>

                  <p style="margin:0;font-size:13px;color:#888">
                    Questions? Reply to this email or contact us at
                    <a href="mailto:{SUPPORT_EMAIL}" style="color:#c97d3e">{SUPPORT_EMAIL}</a>.
                  </p>
                </td></tr>

                <!-- Footer -->
                <tr><td style="background:#fdf7f0;padding:20px;text-align:center;font-size:12px;color:#aaa">
                  © ChonkyChonk — premium fuel for the discerning chonk
                </td></tr>

              </table>
            </td></tr>
          </table>
        </body>
        </html>
        """

    def _render_failure_html(self, e: OrderFailureEmail) -> str:
        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
        <body style="margin:0;padding:0;background:#fdf7f0;font-family:Georgia,serif;color:#3a2e24">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr><td align="center" style="padding:32px 16px">
              <table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">

                <tr><td style="background:#b84c4c;padding:32px;text-align:center">
                  <h1 style="margin:0;color:#fff;font-size:28px;letter-spacing:1px">ChonkyChonk 🐾</h1>
                  <p style="margin:8px 0 0;color:#ffd5d5;font-size:15px">There was a problem with your order</p>
                </td></tr>

                <tr><td style="padding:32px">
                  <p style="margin:0 0 16px;font-size:16px">
                    Hi {e.to.name or "there"}, unfortunately we were unable to process payment for
                    order <strong>#{_short_order_id(e.order_id)}</strong>.
                  </p>
                  <p style="margin:0 0 16px;font-size:14px;color:#888">Reason: {e.error_message}</p>
                  <p style="margin:0;font-size:14px">
                    No charge has been made to your account. Please try again or contact us at
                    <a href="mailto:{e.support_email}" style="color:#b84c4c">{e.support_email}</a>
                    and we'll sort it out.
                  </p>
                </td></tr>

                <tr><td style="background:#fdf7f0;padding:20px;text-align:center;font-size:12px;color:#aaa">
                  © ChonkyChonk — premium fuel for the discerning chonk
                </td></tr>

              </table>
            </td></tr>
          </table>
        </body>
        </html>
        """

    def _render_welcome_html(self, e: WelcomeEmail) -> str:
        greeting_name = e.first_name or "there"
        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
        <body style="margin:0;padding:0;background:#fdf7f0;font-family:Georgia,serif;color:#3a2e24">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr><td align="center" style="padding:32px 16px">
              <table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">

                <!-- Header -->
                <tr><td style="background:#c97d3e;padding:32px;text-align:center">
                  <h1 style="margin:0;color:#fff;font-size:28px;letter-spacing:1px">ChonkyChonk 🐾</h1>
                  <p style="margin:8px 0 0;color:#ffe8cc;font-size:15px">Welcome to the family!</p>
                </td></tr>

                <!-- Body -->
                <tr><td style="padding:32px">
                  <p style="margin:0 0 16px;font-size:16px">
                    Hi {greeting_name}, thanks for joining ChonkyChonk! Your account is all set up
                    and ready to go.
                  </p>
                  <p style="margin:0 0 16px;font-size:14px;color:#666">
                    Browse our treats, place an order, and we'll take care of the rest — chonky
                    tails wagging guaranteed.
                  </p>
                  <p style="margin:0;font-size:13px;color:#888">
                    Questions? Reply to this email or contact us at
                    <a href="mailto:{SUPPORT_EMAIL}" style="color:#c97d3e">{SUPPORT_EMAIL}</a>.
                  </p>
                </td></tr>

                <!-- Footer -->
                <tr><td style="background:#fdf7f0;padding:20px;text-align:center;font-size:12px;color:#aaa">
                  © ChonkyChonk — premium fuel for the discerning chonk
                </td></tr>

              </table>
            </td></tr>
          </table>
        </body>
        </html>
        """

    # ------------------------------------------------------------------
    # Plain-text fallbacks
    # ------------------------------------------------------------------

    def _render_confirmation_text(self, e: OrderConfirmationEmail) -> str:
        item_lines = "\n".join(
            f"  {item['name']} x{item['quantity']} — ${item['line_total']} {e.currency}"
            for item in e.items
        )
        promo_line = f"  Promo ({e.promotion_code}): -${e.discount} {e.currency}\n" if e.promotion_code else ""
        notes_section = f"\nDelivery instructions:\n{e.customer_notes}\n" if e.customer_notes else ""
        return dedent(f"""\
            ChonkyChonk — Order #{_short_order_id(e.order_id)} Confirmed

            Hi {e.to.name or "there"},

            Your order is confirmed and will be shipped to:
            {e.shipping_name}, {e.shipping_address}

            Items:
            {item_lines}
            {notes_section}
            {promo_line}Subtotal:  ${e.subtotal} {e.currency}
            Tax:       ${e.tax} {e.currency}
            Shipping:  ${e.shipping_fee} {e.currency}
            Total:     ${e.total_amount} {e.currency}

            Questions? Contact {SUPPORT_EMAIL}

            © ChonkyChonk
        """)

    def _render_welcome_text(self, e: WelcomeEmail) -> str:
        greeting_name = e.first_name or "there"
        return dedent(f"""\
            ChonkyChonk — Welcome!

            Hi {greeting_name},

            Thanks for joining ChonkyChonk! Your account is all set up and ready to go.

            Questions? Contact {SUPPORT_EMAIL}

            © ChonkyChonk
        """)

    def _render_failure_text(self, e: OrderFailureEmail) -> str:
        return dedent(f"""\
            ChonkyChonk — Problem with Order #{_short_order_id(e.order_id)}

            Hi {e.to.name or "there"},

            We were unable to process payment for order #{_short_order_id(e.order_id)}.

            Reason: {e.error_message}

            No charge has been made to your account.
            Please try again or contact {e.support_email} for help.

            © ChonkyChonk
        """)
