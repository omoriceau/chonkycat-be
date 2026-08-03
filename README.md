# ChonkyChonk Backend

[![Quality gate status](https://sonarcloud.io/api/project_badges/measure?project=omoriceau_chonkycat-be&metric=alert_status&token=7a4ae696822ec0183f38a4eb8401a4e1c0230f54)](https://sonarcloud.io/summary/new_code?id=omoriceau_chonkycat-be)

AWS Lambda backend for the ChonkyChonk cat food store — an AWS SAM stack
(API Gateway + Lambda + DynamoDB + Cognito + EventBridge + SES + Stripe),
deployed to `us-east-1`.

## Structure

```
chonky-cat-be/
├── template.yaml            # SAM template — the source of truth for every AWS resource
├── samconfig.toml           # Per-environment deploy config (stack name, region, parameters)
├── ci-deploy.sh             # What CI runs: verify infra exists, sam build, sam deploy
├── deploy-products.sh       # Manual, interactive: provisions a new environment's infra
│
├── shared/python/shared/    # Lambda layer — imported as `from shared.x import y`
│   ├── db.py                 # DynamoDB table helpers
│   ├── cors.py                # CORS headers (dev echoes any Origin; other envs allow *.chonkycat.ca)
│   ├── secrets.py             # Secrets Manager lookups
│   └── events.py              # EventBridge source/detail-type constants + payload shapes
│
└── lambdas/
    ├── products/             # Product catalog CRUD + image upload
    ├── orders/                # Cart + order placement
    ├── users/                 # User CRUD, Cognito account management, self-service profile
    ├── payments_api/          # POST /payments — creates the Stripe PaymentIntent
    ├── stripe_intent/         # Calls the Stripe API (invoked by payments_api, not API Gateway)
    ├── stripe_webhook/        # Receives Stripe webhook events, updates order/payment status
    └── email_service/         # Sends transactional email in response to EventBridge events
        └── email_service/     # Provider abstraction (base.py, factory.py, ses_provider.py)
```

Each `lambdas/<name>/` directory is its own independently deployable
package with its own `requirements.txt`, `pytest.ini`, and `tests/`.

## API

All routes are on one API Gateway REST API, one stage per environment
(`/dev`, `/prod`, …).

| Method | Path | Lambda | Notes |
|--------|------|--------|-------|
| GET | `/products` | products | Public |
| GET | `/products/{productid}` | products | Public |
| POST / PUT / PATCH / DELETE | `/products[/{productid}]` | products | Admin |
| POST | `/products/{productid}/image` | products | Admin — uploads to `chonky-images-<env>` |
| POST | `/products/image` | products | Admin — upload by SKU |
| GET / POST | `/orders` | orders | Admin |
| GET / PUT / DELETE | `/orders/{orderId}` | orders | Admin |
| GET | `/users/orders` | orders | Customer self-service (bearer token) |
| GET / POST | `/cart`, `/cart/items` | orders | Guest or logged-in |
| PUT / DELETE | `/cart/items/{productId}` | orders | Guest or logged-in |
| POST | `/cart/{orderId}/checkout` | orders | Guest or logged-in |
| POST | `/cart/claim` | orders | Requires customer Cognito auth |
| GET / POST / DELETE | `/users`, `/users/{userId}` | users | Admin |
| GET / PUT | `/users/{userId}` | users | Customer self-service — caller must own the `{userId}` |
| GET / PUT | `/admin/users/{userId}` | users | Admin equivalent of the self-service routes |
| POST | `/payments` | payments_api | Creates an order's Stripe PaymentIntent |
| POST | `/webhook` | stripe_webhook | Stripe calls this directly (signature-verified, not Cognito) |

Two Cognito user pools are involved: an admin pool (`CognitoUserPoolId`,
used by `users` for account management via `AdminCreateUser` etc.) and a
customer-facing pool (`CustomerCognitoUserPoolId`, used as an API Gateway
authorizer on the self-service/cart-claim routes above). Admin-route
authorization is attached outside this template.

## Event flow

`orders` and `users` publish onto the `chonkychonk-bus` EventBridge bus;
`email_service` is the only current subscriber:

```
orders   ──OrderCreated──────►  chonkychonk-bus  ──►  email_service  ──►  order confirmation email
orders   ──LowStockDetected──►  chonkychonk-bus  ──►  email_service  ──►  (low-stock branch)
users    ──UserCreated───────►  chonkychonk-bus  ──►  email_service  ──►  welcome email
```

`payments_api` and `stripe_webhook` also publish events onto the same bus
(`PaymentIntentCreated`, `PaymentSucceeded`, `PaymentFailed`) — nothing
currently subscribes to those; they're available for a future consumer.

The bus itself (`chonkychonk-bus`) is created outside CloudFormation, by
`deploy-products.sh`, before the stack is deployed — `ci-deploy.sh`
verifies it exists rather than declaring it as a stack resource.

### EventBridge rule

| Source | Detail-type | Target |
|--------|-------------|--------|
| `chonkychonk.orders` | `OrderCreated`, `OrderFailure`, `LowStockDetected` | email_service |
| `chonkychonk.users` | `UserCreated` | email_service |

## Environment variables

Set on every Lambda via `template.yaml`'s `Globals`:

| Variable | Description |
|----------|-------------|
| `EVENT_BUS_NAME` | `chonkychonk-bus` |
| `STRIPE_SECRET_KEY_SECRET_NAME` | Secrets Manager name for the Stripe secret key |
| `STRIPE_WEBHOOK_SECRET_NAME` | Secrets Manager name for the Stripe webhook signing secret |
| `ENVIRONMENT` | `dev` / `staging` / `prod` |
| `DEV_EMAIL` | Recipient for SES sandbox-mode test sends |

Per-function additions:

| Lambda | Variable | Description |
|--------|----------|-------------|
| products | `PRODUCTS_TABLE_NAME`, `PRODUCT_IMAGES_BUCKET` | DynamoDB table; S3 bucket for product images |
| orders | `ORDERS_TABLE_NAME`, `PRODUCTS_TABLE_NAME`, `PROMOTIONS_TABLE_NAME`, `CUSTOMER_COGNITO_USER_POOL_ID`, `CUSTOMER_COGNITO_APP_CLIENT_ID` | Tables read/written; customer pool for bearer-token verification |
| users | `USERS_TABLE_NAME`, `COGNITO_USER_POOL_ID` | Table; admin pool for account management |
| payments_api | `STRIPE_INTENT_FUNCTION_ARN`, `PAYMENTS_TABLE_NAME`, `ORDERS_TABLE_NAME`, `USERS_TABLE_NAME` | Invokes stripe_intent directly; reads/writes these tables |
| stripe_webhook | `PAYMENTS_TABLE_NAME`, `ORDERS_TABLE_NAME` | Updates payment/order status |
| email_service | `EMAIL_FROM_ADDRESS` | `no-reply@<SES domain>`, set once SES is provisioned; falls back to a hardcoded default until then |

## IAM permissions

| Lambda | Permissions |
|--------|-------------|
| products | DynamoDB CRUD on the products table; `s3:PutObject` on `chonky-images-<env>/img/*` |
| orders | DynamoDB CRUD on orders + products, read on promotions, `TransactWriteItems` on orders + products, `events:PutEvents` |
| users | DynamoDB CRUD on users, `TransactWriteItems` on users, `events:PutEvents`, Cognito `AdminCreateUser`/`AdminSetUserPassword`/`AdminDeleteUser`/`AdminUpdateUserAttributes` scoped to the admin pool |
| payments_api | DynamoDB CRUD on payments, read on orders + users, `events:PutEvents`, `lambda:InvokeFunction` on stripe_intent |
| stripe_intent | `secretsmanager:GetSecretValue` on the Stripe secret key |
| stripe_webhook | `secretsmanager:GetSecretValue` on the webhook secret, DynamoDB CRUD on payments + orders, `events:PutEvents` |
| email_service | `ses:SendEmail`, `ses:SendRawEmail` — no DynamoDB, no Stripe access |

## Secrets

Two Secrets Manager secrets per environment, named `chonky/<environment>/*`:

| Secret | Used by |
|--------|---------|
| `chonky/<env>/stripe_secret_key` | stripe_intent |
| `chonky/<env>/stripe_webhook_secret` | stripe_webhook |

Create them with:

```bash
aws secretsmanager create-secret \
  --name chonky/dev/stripe_secret_key \
  --secret-string "sk_test_..." \
  --region us-east-1

aws secretsmanager create-secret \
  --name chonky/dev/stripe_webhook_secret \
  --secret-string "whsec_..." \
  --region us-east-1
```

## Local development

Copy [.env.example](.env.example) to `.env.local` (already gitignored) and
fill in real values:

```bash
cp .env.example .env.local
```

Fastest path to running something locally — set `LOCAL_MOCK_DB=true` and
skip DynamoDB/AWS entirely; `shared/python/shared/mock_db.py` swaps in an
in-memory table implementation. Without it, the table name variables need
to point at real DynamoDB tables (e.g. the `dev` ones, if you have AWS
access) and everything else — Cognito pool IDs, the Stripe key, SES
sender address — needs real values too, or whichever code path touches
them will fail.

`.env.local` itself isn't loaded automatically by anything in this repo
(no process reads it directly) — it's a reference for setting these as
real environment variables in however you run a Lambda locally (`sam
local invoke`, a debugger config, `pytest` fixtures, etc.).

## Adding a new email type

1. Add a dataclass for the email's context to `lambdas/email_service/email_service/base.py`
2. Add the corresponding abstract method to `EmailProvider` in the same file
3. Implement it in `lambdas/email_service/email_service/ses_provider.py`
4. Add a handler branch in `lambdas/email_service/lambda_handler.py` for the new `detail-type`
5. If the event isn't already covered, add its `detail-type` to `EmailServiceFunction`'s
   `OrderEmailEvent` pattern in `template.yaml`

## CI/CD

GitHub Actions (`.github/workflows/`) builds every PR against `master`,
then on push to `master` deploys dev automatically and prod behind a
manual approval gate. A SonarQube scan runs on the same triggers. Full
setup walkthrough (IAM roles, environment config) is in
[CICD.md](CICD.md) — quick reference below.

### Required GitHub secrets

Nothing deploys until these are set — there are no defaults for AWS
credentials.

**Repo secrets** (Settings → Secrets and variables → Actions):

| Secret | Required | Used by |
|--------|----------|---------|
| `AWS_DEPLOY_ROLE_ARN` | Yes | `deploy-dev` job |
| `SONAR_TOKEN` | No — scan skips with a warning if unset | SonarQube workflow |
| `SONAR_HOST_URL` | No — only for a self-hosted SonarQube Server | SonarQube workflow |

**`prod` Environment secret** (Settings → Environments → `prod` → Secrets
— this is also where you configure the required-reviewer approval that
gates prod deploys):

| Secret | Required | Used by |
|--------|----------|---------|
| `AWS_DEPLOY_ROLE_ARN_PROD` | Yes | `deploy-prod` job |

That's the only prod secret — table names, Cognito pool IDs, SES domain,
and alert email all live in `samconfig.toml`'s `[prod.deploy.parameters]`
section instead (none of them are actually sensitive). `ci-deploy.sh`
refuses to deploy an environment whose config still has a `TODO-*`
placeholder. Get the two role ARNs by running `./setup-github-actions-oidc.sh`
(dev) and `./setup-github-actions-oidc.sh --environment prod` — see
[CICD.md](CICD.md) for the full command list.
