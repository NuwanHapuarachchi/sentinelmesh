import json

import boto3
import pytest
from moto import mock_aws

from scripts import put_secrets


def test_requires_the_api_key():
    with pytest.raises(SystemExit):
        put_secrets.build({}, {})


def test_keeps_an_existing_dlr_secret_and_generates_one_otherwise():
    assert put_secrets.build({"SMS_PROVIDER_API_KEY": "k"}, {"dlr_secret": "old"})["dlr_secret"] == "old"
    assert len(put_secrets.build({"SMS_PROVIDER_API_KEY": "k"}, {})["dlr_secret"]) >= 32
    assert put_secrets.build({"SMS_PROVIDER_API_KEY": "k", "SMS_DLR_SECRET": "new"}, {"dlr_secret": "old"})["dlr_secret"] == "new"


def test_writes_the_secret_without_printing_values(monkeypatch, capsys):
    for k, v in {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x", "AWS_DEFAULT_REGION": "ap-southeast-2",
                 "SMS_PROVIDER_API_KEY": "super-secret-key", "SMS_SENDER_ID": "SentinelMsh"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SMS_DLR_SECRET", raising=False)
    with mock_aws():
        sm = boto3.client("secretsmanager", region_name="ap-southeast-2")
        sm.create_secret(Name="sentinelmesh/sms-provider", SecretString=json.dumps({"api_key": "", "sender_id": "", "dlr_secret": ""}))
        assert put_secrets.main() == 0
        stored = json.loads(sm.get_secret_value(SecretId="sentinelmesh/sms-provider")["SecretString"])
    assert stored["api_key"] == "super-secret-key" and stored["sender_id"] == "SentinelMsh" and stored["dlr_secret"]
    out = capsys.readouterr().out
    assert "super-secret-key" not in out and stored["dlr_secret"] not in out
