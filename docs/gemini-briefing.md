# Optional Gemini zone briefing

Insights, anomaly review and a qualitative trend outlook are available on demand to duty officers and meteorology users. This is evidence interpretation, not a trained anomaly detector or validated flood prediction. It cannot issue, suppress or clear warnings.

## Infrastructure

No new Terraform resources are declared. The existing console Lambda, authenticated REST API, rate-limit table, web config and SMS-provider secret are updated. The Gemini key is a separate `gemini_api_key` field in that existing secret. Existing SMS fields are preserved, including when SMS credentials are reconfigured. This intentionally shares a secret to satisfy the prototype constraint; separating credentials would be preferable for production isolation.

Terraform changes must go through the normal plan/apply workflow. The feature defaults to disabled. No live configuration or deployment is performed by these code changes.

## Enable after review

1. Choose a Gemini model available to your Google account that supports `generateContent` with JSON schema. Check its free quota and data-use terms.
2. Set `GEMINI_API_KEY` securely in a trusted terminal or masked, protected CI variable. With authorized AWS credentials, explicitly run `python scripts/configure_gemini.py`. It only updates the existing secret and never prints the key. Clear the environment variable afterward. Do not put keys in Terraform variables, browser code, mobile code or Git.
3. Set `TF_VAR_gemini_enabled=true` and `TF_VAR_gemini_model` to that model ID, review the plan, then apply through the existing pipeline.
4. Sign in as a duty officer or meteorology user, select a public zone with fresh readings, and click **Analyze zone**.

## Evidence and controls

Only numeric gauge/rain/tilt/forecast observations, timestamps, existing tier/blind status and configured water thresholds are sent. Names, source IDs, coordinates, resident reports, reporter hashes and subscriber data are excluded. The stored gauge history holds up to six samples. Trend interpretation requires at least three distinct recent samples spanning two minutes. Zone state older than five minutes is rejected. Missing history forces an insufficient-data outlook.

Responses have three bounded sections and must cite known evidence IDs. Numeric model claims and obvious warning instructions are rejected; these checks cannot prove every sentence factually correct. Officers must compare statements with the displayed source snapshot. The interface escapes model text, preserves error handling and marks cached evidence with its timestamp. A refreshed zone snapshot invalidates the displayed briefing.

Gemini calls have a ten-second network timeout, a bounded response and output-token budget, and no automatic provider retries. A shared DynamoDB conditional counter permits at most five uncached attempts per minute and thirty per UTC day, across Lambda instances. Failures also consume attempts. Exact evidence snapshots are cached for two minutes. Concurrent cache misses can call the provider more than once within those limits. The limits bound requests, not exact spending; Google quotas still apply. Free-tier data handling must be reviewed before using real operational readings.

Disable by setting `gemini_enabled=false` and applying. The core scoring, approval, delivery and local fallback paths never call Gemini. API outages, rejected responses and exhausted quotas return a briefing-unavailable message without changing warning state.

## Verification

On 2026-10-10, a live synthetic-evidence smoke test passed with `gemini-3.5-flash-lite`, using the same schema and output validation as the backend. The key was configured in the existing AWS secret, with other fields verified as preserved. Local deployment settings are in ignored `terraform/app/gemini.auto.tfvars`; CI must explicitly set the two `TF_VAR_gemini_*` variables because this local file is not pushed. This does not mean the feature branch has been deployed.

Run Python unit tests, Node console tests, Ruff and Terraform formatting/validation in the normal CI environment. Provider tests use fake responses, never live alerts. A real Gemini smoke test and deployment require a configured key and explicit deployment review.

Sources: https://ai.google.dev/gemini-api/docs/structured-output and https://ai.google.dev/gemini-api/docs/api-key
