"""Explicitly add a Gemini key to the existing secret. Never prints credentials."""
import json
import os

import boto3


def main():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise SystemExit("Set GEMINI_API_KEY securely before running this script")
    client = boto3.client("secretsmanager", region_name="ap-southeast-2")
    secret_id = "sentinelmesh/sms-provider"
    current = json.loads(client.get_secret_value(SecretId=secret_id)["SecretString"] or "{}")
    current["gemini_api_key"] = key
    client.put_secret_value(SecretId=secret_id, SecretString=json.dumps(current))
    print("Gemini key updated in the existing secret; other fields preserved")


if __name__ == "__main__":
    main()
