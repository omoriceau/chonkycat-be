"""
websocket/lambda_handler.py

API Gateway WebSocket handler for the checkout page.

No database required — connection_id is self-contained:
  1. Browser connects  →  API GW returns a connection_id
  2. Browser includes connection_id in the POST /orders body
  3. Orders Lambda persists it alongside the order
  4. Payments Lambda reads it from the EventBridge event and pushes
     the payment result directly to the browser via post_to_connection

Routes:
  $connect     — client connects; no storage needed, just return 200
  $disconnect  — client disconnects; no cleanup needed
  $default     — catch-all for unexpected frames

Environment Variables:
  - APIGW_ENDPOINT   e.g. https://{api-id}.execute-api.{region}.amazonaws.com/{stage}
                     (kept here for the management client used by PaymentsFunction,
                      not needed by the WebSocket Lambda itself)
"""

import json
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def ok(body: str = "OK") -> dict:
    return {"statusCode": 200, "body": body}


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def handle_connect(event: dict) -> dict:
    """
    Client opens a WebSocket connection.

    API Gateway assigns a connection_id automatically — the browser reads it
    from the onopen event and includes it in the POST /orders request body.
    Nothing needs to be stored here.
    """
    connection_id = event["requestContext"]["connectionId"]
    logger.info("WebSocket connected: %s", connection_id)
    return ok()


def handle_disconnect(event: dict) -> dict:
    """
    Client closed the tab or the connection dropped.

    No cleanup required. If a payment result is pushed to a gone connection,
    PaymentsFunction handles the GoneException gracefully — the order still
    processes and the confirmation email covers the user.
    """
    connection_id = event["requestContext"]["connectionId"]
    logger.info("WebSocket disconnected: %s", connection_id)
    return ok()


def handle_default(event: dict) -> dict:
    """Catch-all for any frames not matched by a named route."""
    logger.info("Unhandled WS frame: %s", json.dumps(event, default=str))
    return ok()


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    route = event.get("requestContext", {}).get("routeKey", "$default")

    routes = {
        "$connect":    handle_connect,
        "$disconnect": handle_disconnect,
        "$default":    handle_default,
    }

    handler = routes.get(route, handle_default)
    return handler(event)
