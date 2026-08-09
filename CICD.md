# CI/CD

## How it works

`.github/workflows/deploy.yml` triggers on pushes and PRs against
`master`:

- **`build`** — every PR and push: `sam build` + `sam validate --lint`.
  No AWS access.
- **`deploy-dev`** — every push to `master`: runs
  `ci-deploy.sh --environment dev`. No approval gate.
- **`deploy-prod`** — every push to `master`, gated behind the
  `vars.PROD_ENABLED` repo variable and the `prod` GitHub Environment's
  required-reviewer approval. Runs `ci-deploy.sh --environment prod`.

`ci-deploy.sh` is the only deploy logic CI runs. For a given environment
it:

1. Reads that environment's `[<environment>.deploy.parameters]` section
   from `samconfig.toml` — stack name, region, S3 bucket, and the full
   `parameter_overrides` list. This file is the source of truth for what
   gets deployed; `ci-deploy.sh` doesn't keep its own copy of the
   parameter list.
2. Refuses to proceed if that section is missing, or if any
   `parameter_overrides` value is still a `TODO-*` placeholder.
3. Verifies the DynamoDB tables, S3 bucket, and EventBridge bus it needs
   already exist — it never creates or deletes AWS resources.
4. Runs `sam build` then `sam deploy --config-env <environment>`.

`deploy-products.sh` is a separate, more capable script for provisioning
an environment from scratch or changing its infrastructure by hand — SES
domain setup, the S3/EventBridge bus creation, an API Gateway custom
domain mapping, and an EventBridge rule created outside CloudFormation. It
also handles a stack stuck in `UPDATE_ROLLBACK_FAILED` by deleting it.
None of that runs in CI; `deploy-products.sh` is a manual, one-off tool,
not something to automate.

`.github/workflows/sonarqube.yml` runs a SonarCloud scan on the same
triggers. Before scanning, it loops over `lambdas/*/` and, for any lambda
with a `requirements-dev.txt` (currently `products`, `stripe_webhook`,
`email_service`, `orders`, `payments_api`, and `users`), installs that
lambda's dev deps into a throwaway venv and runs its `tests/` with
`pytest-cov`, writing `lambdas/<name>/coverage.xml`. Each lambda's suite
runs as its own subprocess — running two lambdas' `tests/` packages in one
`pytest` process collides on the `tests.conftest` module name — and from
the repo root, not `cd`'d into the lambda dir, since `python -m pytest`
needs the repo root on `sys.path` for `shared` imports to resolve.
`sonar-project.properties`' `sonar.python.coverage.reportPaths` picks up
`lambdas/*/coverage.xml` via wildcard, so a lambda gets coverage the moment
it gains a `requirements-dev.txt` — no other wiring needed. Lambdas without
one (currently just `stripe_intent`, which has no `tests/` yet) are still
scanned statically but show no coverage numbers.

The coverage command is `pytest ... --cov=. --cov-config=.coveragerc`, run
from the repo root — not `--cov=<lambda dir> --cov=shared`. Two separate
`--cov` roots make pytest-cov compute each file's XML path relative to
whichever root matched, so a lambda's own `db.py` and `shared/db.py` both
collapse to the bare filename `db.py`; coverage.py then silently keeps
only one class entry and drops the other's data entirely (observed:
`shared/db.py`'s all-zero coverage was clobbering every lambda's own,
usually much-better-covered, `db.py`). Using the repo root as the sole
`--cov` target makes every path fully qualified and unambiguous
(`lambdas/orders/db.py` vs `shared/db.py`) while still only reporting
files that lambda's own tests actually imported — coverage.py's
"list unmeasured files too" pass only walks packages that were at least
partially imported (e.g. `shared`, via `from shared.cors import ...`), so
unrelated lambdas don't leak into another lambda's report.

## Setup

### 1. AWS deploy roles (OIDC)

Both deploy jobs authenticate via GitHub's OIDC provider — no long-lived
AWS keys stored anywhere. Each environment gets its own IAM role:

```bash
./setup-github-actions-oidc.sh                    # dev role
./setup-github-actions-oidc.sh --environment prod  # prod role
```

This creates or updates:
- The account's OIDC provider trusting `token.actions.githubusercontent.com`
  (created once, shared across roles).
- An IAM role — `chonky-cat-be-github-actions-deploy` (dev) or
  `chonky-cat-be-github-actions-deploy-prod` (prod) — assumable only by
  workflow runs triggered by a push to this repo's `master` branch.
- Permissions covering what `ci-deploy.sh` / `deploy-products.sh` and
  `sam deploy` need: CloudFormation, Lambda, API Gateway, IAM
  (`CAPABILITY_IAM`, for SAM-managed Lambda execution roles), EventBridge,
  the `chonkychonk-sam-artifacts-*` S3 bucket, and DynamoDB
  `DescribeTable`.

Set the printed ARN as a secret (the script prints the exact command):

```bash
gh secret set AWS_DEPLOY_ROLE_ARN --repo omoriceau/chonkycat-be --body "<dev arn>"
gh secret set AWS_DEPLOY_ROLE_ARN_PROD --repo omoriceau/chonkycat-be --env prod --body "<prod arn>"
```

`AWS_DEPLOY_ROLE_ARN_PROD` must be set on the `prod` **Environment**, not
as a plain repo secret.

### 2. The `prod` Environment

Create it under repo Settings → Environments → `prod`, then:

1. **Required reviewers** — at least one person who must approve before
   `deploy-prod` runs. Combined with `vars.PROD_ENABLED`, this is what
   keeps prod from deploying automatically on every push.
2. **Secret**: `AWS_DEPLOY_ROLE_ARN_PROD` — the only prod-specific GitHub
   secret needed. Everything else (table names, Cognito pool IDs, SES
   domain, alert email) lives in `samconfig.toml`'s
   `[prod.deploy.parameters]` section — none of it is actually sensitive,
   and keeping it in one versioned file is what makes `ci-deploy.sh`'s
   checks meaningful. Fill in that section's `TODO-*` placeholders with
   real values; `ci-deploy.sh` won't deploy while any remain.

Prod's infrastructure (DynamoDB tables, S3 bucket, EventBridge bus,
Cognito pools) also has to exist before any of this works — nothing
provisions it automatically. Run
`deploy-products.sh --environment prod` manually, once, to provision it.

Once the role, secret, infra, and `samconfig.toml` values are all in
place, flip on automatic prod deploys:

```bash
gh variable set PROD_ENABLED --repo omoriceau/chonkycat-be --body "true"
```

(or repo Settings → Secrets and variables → Actions → Variables). Until
set, `deploy-prod` is skipped — not failed — on every push.

### 3. SonarQube

`sonar-project.properties` at the repo root holds `sonar.projectKey` and
`sonar.organization` — confirm these match your SonarCloud project (or
self-hosted server) before the scan will succeed.

1. Create a project at [sonarcloud.io](https://sonarcloud.io) (or point
   at a self-hosted SonarQube server).
2. Generate a token (SonarCloud: My Account → Security) and set it:

```bash
gh secret set SONAR_TOKEN --repo omoriceau/chonkycat-be --body "<token>"
```

3. Self-hosted SonarQube Server only: also set the `SONAR_HOST_URL` secret
   to the server's URL, **and** add a `SONAR_HOST_URL: ${{ secrets.SONAR_HOST_URL }}`
   line to the `env:` block of the "SonarQube Scan" step in
   `sonarqube.yml` — the two changes need to land together. That env var
   isn't there by default: GitHub Actions turns a nonexistent secret into
   an *empty string*, not a missing variable, and the scanner CLI fails
   outright ("URI with undefined scheme") if `SONAR_HOST_URL` is present
   but empty, rather than quietly falling back to SonarCloud's default
   the way a genuinely absent variable would. Skip this whole step for
   SonarCloud.

Until `SONAR_TOKEN` is set, the workflow no-ops with a warning instead of
failing every PR/push.

## Troubleshooting

If a `deploy-dev`/`deploy-prod` run hits `AccessDenied`, check the failing
step's log for which action was denied, add it to the relevant policy in
`setup-github-actions-oidc.sh` — most deploy-time permissions (CloudFormation,
Lambda, EventBridge, API Gateway) live in the shared customer-managed policy
`chonky-cat-be-deploy-services-scoped`; IAM PassRole, the SAM artifacts
bucket, and DynamoDB DescribeTable live in the role's inline policy — then
re-run that script for the affected environment. It's idempotent, and since
the services policy is shared by both roles, running it for either
environment updates permissions for both.
