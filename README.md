# SentinelMesh

Multi-hazard early warning and alert delivery prototype for **Beauty of Cloud 2.0** (Scenario 1), team **TheByteForce**.

Everything is Terraform and everything runs in GitLab CI/CD. Nobody provisions from a laptop.

## Layout

| Path | What |
|---|---|
| `terraform/bootstrap/` | The pipeline's own AWS identity: CI user, deploy role, workload permissions boundary, and the GitLab CI variables that hold the CI key |
| `terraform/app/` | The prototype (root module) |
| `terraform/modules/` | `shared`, and later `cell`, `api`, `web`, `observability` |
| `services/` | Lambda code (Python 3.13, stdlib + boto3) |
| `web/` | Dashboard, officer console, simulated phone |
| `tests/unit`, `tests/e2e`, `tests/load` | Tests run by the pipeline |
| `scripts/gen_zones.py` | Generates the 80 hazard zones + test zones |
| `scripts/verify_teardown.py` | Read-only check that nothing is left after a destroy |

## Account rules

- Region `ap-southeast-2` only (organization policy).
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

Pushes to `main` run lint, unit tests, the IaC scan and an app plan. Other branches and MRs run lint and tests only; they never get AWS credentials.

## One-time setup

1. In the IAM console, create a temporary access key on an admin user. Add it in **Settings → CI/CD → Variables** as `BOOTSTRAP_AWS_ACCESS_KEY_ID` (masked, protected) and `BOOTSTRAP_AWS_SECRET_ACCESS_KEY` (masked, hidden, protected).
2. Create a GitLab personal access token with `api` scope. Add it as `GITLAB_TOKEN` (masked, hidden, protected).
3. Run the pipeline with `stack=bootstrap`, `action=apply`. Terraform creates the CI user and deploy role and writes `SM_AWS_ACCESS_KEY_ID`, `SM_AWS_SECRET_ACCESS_KEY` and `SM_AWS_DEPLOY_ROLE_ARN` into the project variables.
4. Delete the temporary admin key and the `BOOTSTRAP_*` variables.

Optional variables: `TF_VAR_officer_email` (alerts and Cognito invite), `SMS_PROVIDER_URL` and `SMS_PROVIDER_API_KEY` (third-party SMS, later).

## Teardown after the competition

1. Run `stack=app`, `action=destroy`. The `verify-teardown` job must pass.
2. Add a temporary admin key as `BOOTSTRAP_*` again and run `stack=bootstrap`, `action=destroy`. This removes the CI user, role, boundary and the `SM_AWS_*` variables.
3. Delete the admin key, the `BOOTSTRAP_*` and `GITLAB_TOKEN` variables, and revoke the token. Optionally delete the Terraform states under **Operate → Terraform states**.
