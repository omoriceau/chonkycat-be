# ChonkyChonk Backend

AWS Lambda backend for the ChonkyChonk cat food store.

## Structure

```
chonkychonk-backend/
├── shared/
│   └── events.py                   # Canonical EventBridge event definitions (single source of truth)
│
├── db/
│   └── migration_websocket.sql     # Adds ws_connections table + orders.connection_id
│
└── lambdas/
    ├── products/
    │   └── lambda_handler.py       # GET /products
    │
    ├── users/
    │   └── lambda_handler.py       # GET /users/{id}  + UserRegistered emit helper
    │
    ├── orders/
    │   ├── lambda_handler.py       # POST /orders
    │   ├── models.py               # Request dataclasses + validation
    │   └── service.py              # DB writes, OrderCreated emit, LowStockDetected emit
    │
    ├── payments/
    │   ├── lambda_handler.py       # EventBridge target — charge/refund, WS notify, event emit, SNS alert
    │   ├── models.py               # Charge/refund payload models
    │   ├── service.py              # PaymentService — provider orchestration + DB writes
    │   └── providers/
    │       ├── base.py             # PaymentProvider ABC + value objects
    │       ├── stripe_provider.py  # Stripe implementation
    │       └── factory.py          # Payment provider registry
    │
    ├── email/
    │   ├── lambda_handler.py       # Email router — handles all 7 event types
    │   ├── providers/
    │   │   ├── base.py             # EmailProvider ABC + all context dataclasses
    │   │   ├── ses_provider.py     # AWS SES implementation
    │   │   └── factory.py          # Email provider registry
    │   └── templates/
    │       └── renderer.py         # Pure render functions → (subject, html, text)
    │
    └── websocket/
        └── lambda_handler.py       # $connect / $disconnect / notify routes
```

## Event flow

```
Frontend
  │
  ├─ Opens WebSocket → receives connection_id
  └─ POST /orders (includes connection_id)
          │
          ▼
    Orders Lambda
      ├─ validates, prices, persists order
      ├─ emits OrderCreated       ──────────────────────────► Payment Lambda
      └─ emits LowStockDetected (if any item crossed threshold) ─► Email Lambda
                                                                      │
                                                      sends low stock alert to ops
    Payment Lambda
      ├─ charges Stripe
      ├─ persists payment to Aurora
      ├─ pushes result to frontend via WebSocket
      ├─ emits PaymentSettled / PaymentFailed ──────────────► Email Lambda
      └─ on provider unreachable: publishes to SNS ─────────► Ops team email/SMS
                                                            Email Lambda
                                                              ├─ PaymentSettled  → order confirmation
                                                              ├─ PaymentFailed   → failure email
                                                              ├─ RefundComplete  → refund confirmation
                                                              ├─ LowStockDetected→ ops stock alert
                                                              ├─ UserRegistered  → welcome email
                                                              ├─ PasswordResetRequest → reset link (Cognito stub)
                                                              └─ OrderSummaryRequest  → order history
```

## EventBridge rules needed

| Rule name                  | Source                  | Detail-type             | Target           |
|----------------------------|-------------------------|-------------------------|------------------|
| route-order-to-payment     | chonkychonk.orders      | OrderCreated            | Payment Lambda   |
| route-events-to-email      | chonkychonk.orders      | LowStockDetected        | Email Lambda     |
|                            | chonkychonk.payments    | PaymentSettled          | Email Lambda     |
|                            | chonkychonk.payments    | PaymentFailed           | Email Lambda     |
|                            | chonkychonk.payments    | RefundComplete          | Email Lambda     |
|                            | chonkychonk.users       | UserRegistered          | Email Lambda     |
|                            | chonkychonk.users       | PasswordResetRequest    | Email Lambda     |
|                            | chonkychonk.users       | OrderSummaryRequest     | Email Lambda     |

The `route-events-to-email` rule can use a single pattern with multiple sources/detail-types.

## SNS topic

`chonkychonk-ops-alerts` — subscribe your ops email address to this topic.
Receives `PaymentUnreachable` alerts when the payment provider cannot be reached
(infrastructure failure, not card declines — those go through the Email Lambda).

## Lambda configuration

| Lambda     | Handler                              | Trigger                        |
|------------|--------------------------------------|-------------------------------|
| products   | `lambda_handler.lambda_handler`      | API GW GET /products           |
| users      | `lambda_handler.lambda_handler`      | API GW GET /users/{id}         |
| orders     | `lambda_handler.lambda_handler`      | API GW POST /orders            |
| payments   | `payments.lambda_handler.lambda_handler` | EventBridge OrderCreated   |
| email      | `email.lambda_handler.lambda_handler`| EventBridge (multiple rules)   |
| websocket  | `lambda_handler.lambda_handler`      | API GW WebSocket               |

## Environment variables

### All Lambdas
| Variable           | Description             |
|--------------------|-------------------------|
| `DB_CLUSTER_ARN`   | Aurora cluster ARN      |
| `DB_SECRET_ARN`    | Secrets Manager ARN     |
| `DB_NAME`          | `chonkychonk`           |
| `EVENT_BUS_NAME`   | default: chonkychonk-bus|

### Payments Lambda
| Variable              | Description                                    |
|-----------------------|------------------------------------------------|
| `PAYMENT_PROVIDER`    | default: `stripe`                              |
| `STRIPE_SECRET_KEY`   | Stripe live or test secret key                 |
| `APIGW_WS_ENDPOINT`   | API GW WebSocket management endpoint           |
| `SNS_OPS_TOPIC_ARN`   | SNS topic for payment-unreachable alerts       |

### Email Lambda
| Variable              | Description                                    |
|-----------------------|------------------------------------------------|
| `EMAIL_PROVIDER`      | default: `ses`                                 |
| `EMAIL_FROM_ADDRESS`  | Verified SES sender address                    |
| `EMAIL_FROM_NAME`     | Display name (default: ChonkyChonk)            |
| `SUPPORT_EMAIL`       | Shown in customer-facing templates             |
| `LOW_STOCK_RECIPIENT` | Internal email for stock alerts                |

### Users Lambda
| Variable                 | Description                          |
|--------------------------|--------------------------------------|
| `COGNITO_ENABLED`        | `true` once Cognito is configured    |
| `COGNITO_USER_POOL_ID`   | Required when COGNITO_ENABLED=true   |
| `COGNITO_APP_CLIENT_ID`  | Required when COGNITO_ENABLED=true   |

### WebSocket Lambda
| Variable          | Description                                |
|-------------------|--------------------------------------------|
| `APIGW_ENDPOINT`  | API GW WebSocket management endpoint       |

## IAM permissions

| Lambda     | Permissions                                                                          |
|------------|--------------------------------------------------------------------------------------|
| products   | `rds-data:ExecuteStatement`, `secretsmanager:GetSecretValue`                         |
| users      | `rds-data:ExecuteStatement`, `secretsmanager:GetSecretValue`, `events:PutEvents`     |
| orders     | `rds-data:ExecuteStatement`, `secretsmanager:GetSecretValue`, `events:PutEvents`     |
| payments   | `rds-data:ExecuteStatement`, `secretsmanager:GetSecretValue`, `events:PutEvents`, `execute-api:ManageConnections`, `sns:Publish` |
| email      | `ses:SendEmail` on verified domain only — **no DB, no Stripe, no API GW**            |
| websocket  | `rds-data:ExecuteStatement`, `secretsmanager:GetSecretValue`, `execute-api:ManageConnections` |

## Adding a new email type

1. Add a constant to `shared/events.py`
2. Add a context dataclass to `email/providers/base.py`
3. Add a render function to `email/templates/renderer.py`
4. Add the method to `EmailProvider` ABC and `SESEmailProvider`
5. Add a handler + route entry in `email/lambda_handler.py`
6. Add an EventBridge rule pointing at the Email Lambda

## Adding a new payment provider

1. Create `lambdas/payments/providers/yourprovider_provider.py` implementing `PaymentProvider`
2. Add one line to `lambdas/payments/providers/factory.py`
3. Set `PAYMENT_PROVIDER=yourprovider` on the Payments Lambda
