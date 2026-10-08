# SentinelMesh

Multi-hazard early warning and alert delivery prototype for **Beauty of Cloud 2.0** (Scenario 1), team **TheByteForce**.

Everything is Terraform and everything runs in GitLab CI/CD. Nobody provisions from a laptop.

## Layout

| Path | What |
|---|---|
| `terraform/bootstrap/` | The pipeline's own AWS identity: CI user, deploy role (limited to roles that carry the workload boundary), and the GitLab CI variables that hold the CI key |
| `terraform/app/` | The prototype (root module): API, web, delivery, operations, and the three cells |
| `terraform/modules/` | `shared` (tables, buckets), `cell` (one detection and delivery cell), `lambda_fn` (one function with its own role and logs) |
| `services/` | Lambda code (Python 3.13, stdlib + boto3) |
| `web/` | Dashboard, officer console, simulated phone |
| `tests/unit`, `tests/web`, `tests/e2e`, `tests/load` | Tests run by the pipeline (unit with moto, the phone page in Node, live end-to-end, burst) |
| `scripts/gen_zones.py` | Generates the 80 hazard zones + test, heartbeat and load zones |
| `scripts/put_secrets.py` | Writes SMS gateway credentials into Secrets Manager (configure-secrets job) |
| `scripts/verify_teardown.py` | Read-only check that nothing is left after a destroy |

## Cells

Cells `a` and `b` each own 40 zones; cell `c` is a warm standby that owns none. Each cell is a full copy of the detection and delivery path (observation table and stream, scorer, escalation workflow, issuer, dispatch planner, senders, queues). Failover is a data change: the console's "Cell A lost" moves A's zones to C in the zones table and the IoT rules route the next reading there. Heartbeat zones H00, H01 and H02 send a test alert through each cell every 10 minutes, so every cell, including the standby, is proven to work.

## Account rules

- Region `ap-southeast-2` only (organization policy). Kinesis is blocked, so the observation bus is a DynamoDB table and stream.
- No retention anywhere: no Object Lock, no customer-managed KMS keys, no PITR, secrets deleted immediately. `terraform destroy` removes everything.

## Running the pipeline

**Build → Pipelines → Run pipeline** on `main`, then pick the inputs:

| Input | Values |
|---|---|
| `stack` | `app` (default) or `bootstrap` |
| `action` | `plan` (default), `apply`, `destroy`. Apply and destroy run straight after the plan |
| `run_e2e` | run end-to-end tests after an app apply (default on) |
| `run_load_test` | run the burst test after an app apply (default off) |

Or from a terminal:

```bash
glab ci run -b main --input stack:string(app) --input action:string(apply)
```

Pushes to `main` run lint, unit and web tests, the IaC and secret scans and an app plan. Other branches and MRs run lint, tests and scans only; they never get AWS credentials.

## One-time setup

1. In the IAM console, create a temporary access key on an admin user. Add it in **Settings → CI/CD → Variables** as `BOOTSTRAP_AWS_ACCESS_KEY_ID` (masked, protected) and `BOOTSTRAP_AWS_SECRET_ACCESS_KEY` (masked, hidden, protected).
2. Create a GitLab personal access token with `api` scope. Add it as `GITLAB_TOKEN` (masked, hidden, protected).
3. Run the pipeline with `stack=bootstrap`, `action=apply`. Terraform creates the CI user and deploy role and writes `SM_AWS_ACCESS_KEY_ID`, `SM_AWS_SECRET_ACCESS_KEY` and `SM_AWS_DEPLOY_ROLE_ARN` into the project variables.
4. Delete the temporary admin key and the `BOOTSTRAP_*` variables.

Bootstrap does not need the app stack to exist, so this order works on a fresh account. The deploy role can create the workload permissions boundary (`terraform/app/iam.tf`) but is denied new versions of it, so it can never widen its own limits. To change the boundary, run the app stack once as an admin (temporarily add the `BOOTSTRAP_*` key and remove `SM_AWS_DEPLOY_ROLE_ARN`).

Optional variables: `TF_VAR_officer_email` (alarm and budget emails), `TF_VAR_officers` (console logins), and for a real third-party SMS gateway `TF_VAR_sms_provider_url` (plus `TF_VAR_sms_http_options`, see `services/common/sms.py`).

The gateway's credentials never go through Terraform: add `SMS_PROVIDER_API_KEY` (and optionally `SMS_SENDER_ID`, `SMS_DLR_SECRET`) as masked, protected variables and run the manual **configure-secrets** job on `main`, which writes them into Secrets Manager. If `SMS_DLR_SECRET` is not set, one is generated; read it in Secrets Manager (`sentinelmesh/sms-provider`) to configure the gateway's delivery reports.

## Teardown after the competition

1. Run `stack=app`, `action=destroy`. The `verify-teardown` job must pass.
2. Add a temporary admin key as `BOOTSTRAP_*` again and run `stack=bootstrap`, `action=destroy`. This removes the CI user, the deploy role and the `SM_AWS_*` variables (the workload boundary went with the app stack).
3. Delete the admin key, the `BOOTSTRAP_*` and `GITLAB_TOKEN` variables, and revoke the token. Optionally delete the Terraform states under **Operate → Terraform states**.
