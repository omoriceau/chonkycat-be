"""
email/providers/ses_provider.py

AWS SES implementation of EmailProvider.
Delegates all rendering to email/templates/renderer.py.

Environment Variables:
  - EMAIL_FROM_ADDRESS     Verified SES sender
  - EMAIL_FROM_NAME        Display name (default: ChonkyChonk)
  - SUPPORT_EMAIL          Shown in templates (default: EMAIL_FROM_ADDRESS)
  - SES_CONFIGURATION_SET  (optional) for open/click tracking
"""

import logging
import os

import boto3
from botocore.exceptions import ClientError

import email.templates.renderer as renderer
from email.providers.base import (
    EmailProvider,
    OrderConfirmationContext,
    OrderFailureContext,
    RefundConfirmationContext,
    LowStockAlertContext,
    WelcomeEmailContext,
    PasswordResetContext,
    OrderSummaryContext,
)

logger = logging.getLogger(__name__)

FROM_ADDRESS      = os.environ.get("EMAIL_FROM_ADDRESS", "orders@chonkychonk.com")
FROM_NAME         = os.environ.get("EMAIL_FROM_NAME",    "ChonkyChonk")
SUPPORT_EMAIL     = os.environ.get("SUPPORT_EMAIL",      FROM_ADDRESS)
CONFIGURATION_SET = os.environ.get("SES_CONFIGURATION_SET")

# Inject support email into renderer module at import time
renderer.SUPPORT_EMAIL = SUPPORT_EMAIL


class SESEmailProvider(EmailProvider):

    def __init__(self, ses_client=None):
        self._ses = ses_client or boto3.client("ses")

    # ------------------------------------------------------------------
    # EmailProvider interface
    # ------------------------------------------------------------------

    def send_order_confirmation(self, ctx: OrderConfirmationContext) -> bool:
        subject, html, text = renderer.render_order_confirmation(ctx)
        return self._send(ctx.to.formatted(), subject, html, text)

    def send_order_failure(self, ctx: OrderFailureContext) -> bool:
        subject, html, text = renderer.render_order_failure(ctx)
        return self._send(ctx.to.formatted(), subject, html, text)

    def send_refund_confirmation(self, ctx: RefundConfirmationContext) -> bool:
        subject, html, text = renderer.render_refund_confirmation(ctx)
        return self._send(ctx.to.formatted(), subject, html, text)

    def send_low_stock_alert(self, ctx: LowStockAlertContext) -> bool:
        subject, html, text = renderer.render_low_stock_alert(ctx)
        return self._send(ctx.to.formatted(), subject, html, text)

    def send_welcome(self, ctx: WelcomeEmailContext) -> bool:
        subject, html, text = renderer.render_welcome(ctx)
        return self._send(ctx.to.formatted(), subject, html, text)

    def send_password_reset(self, ctx: PasswordResetContext) -> bool:
        subject, html, text = renderer.render_password_reset(ctx)
        return self._send(ctx.to.formatted(), subject, html, text)

    def send_order_summary(self, ctx: OrderSummaryContext) -> bool:
        subject, html, text = renderer.render_order_summary(ctx)
        return self._send(ctx.to.formatted(), subject, html, text)

    # ------------------------------------------------------------------
    # SES send
    # ------------------------------------------------------------------

    def _send(self, to: str, subject: str, html: str, text: str) -> bool:
        kwargs = dict(
            Source=f"{FROM_NAME} <{FROM_ADDRESS}>",
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": subject,  "Charset": "UTF-8"},
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
            logger.info("SES sent | MessageId=%s to=%s subject=%r", resp["MessageId"], to, subject)
            return True
        except ClientError as e:
            logger.error("SES failed | to=%s error=%s", to, e)
            return False
