"""Write the third-party SMS gateway settings into Secrets Manager (run by the manual configure-secrets job).

Terraform only creates an empty placeholder, so the key never enters Terraform state. This reads masked
GitLab CI/CD variables and stores them as the secret dispatch and the public API use:

    SMS_PROVIDER_API_KEY   gateway API key                          (required)
    SMS_SENDER_ID          sender id / mask shown on the handset    (optional)
    SMS_DLR_SECRET         shared secret the gateway sends with delivery reports (optional; generated if
                           absent and an existing one is kept, so read it in the console to configure the gateway)

Values are never printed.

    python scripts/put_secrets.py
"""

import json
import os
import secrets
import sys

import boto3

PREFIX = "sentinelmesh"
REGION = "ap-southeast-2"


def build(env, current):
    api_key = env.get("SMS_PROVIDER_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("SMS_PROVIDER_API_KEY is not set (add it as a masked, protected CI/CD variable)")
    dlr = env.get("SMS_DLR_SECRET", "").strip() or current.get("dlr_secret") or secrets.token_urlsafe(32)
    return {"api_key": api_key, "sender_id": env.get("SMS_SENDER_ID", "").strip(), "dlr_secret": dlr}


def main():
    client = boto3.client("secretsmanager", region_name=REGION)
    secret_id = f"{PREFIX}/sms-provider"
    current = json.loads(client.get_secret_value(SecretId=secret_id)["SecretString"] or "{}")
    value = build(os.environ, current)
    client.put_secret_value(SecretId=secret_id, SecretString=json.dumps(value))
    print(f"updated {secret_id}: api_key set, sender_id {'set' if value['sender_id'] else 'empty'}, "
          f"dlr_secret {'kept' if value['dlr_secret'] == current.get('dlr_secret') else 'set'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
