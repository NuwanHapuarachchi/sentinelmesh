import json
import time

import pytest
from conftest import api_event


@pytest.fixture
def configured(stack, monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    monkeypatch.setenv("GEMINI_SECRET_ARN", stack.secret_arn)
    stack.secrets.put_secret_value(SecretId=stack.secret_arn, SecretString=json.dumps({"gemini_api_key": "test-key"}))
    stack.table("zone-state").put_item(Item={
        "zone_id": "Z07", "updated_at": int(time.time()), "tier": "warning", "doc": "{}",
    })
    return stack.load("console_api.handler")


def request(mod, role="met", zone="Z07"):
    return mod.handler(api_event("POST", "/zones/{id}/briefing", groups=role, path={"id": zone}), None)


def test_access_and_disabled(stack, configured, monkeypatch):
    assert request(configured, role="viewer")["statusCode"] == 403
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    assert request(configured)["statusCode"] == 503
    assert stack.table("ratelimits").scan()["Items"] == []


def test_cache_and_read_only(stack, configured, monkeypatch):
    calls = []

    def generate(data, key, model):
        calls.append(data)
        return {section: [{"text": "Review available evidence.", "evidence": ["assessment"]}]
                for section in ("insights", "anomalies", "outlook")}

    monkeypatch.setattr(configured.briefing, "generate", generate)
    before = stack.table("zone-state").scan()["Items"]
    first = request(configured)
    second = request(configured)
    assert first["statusCode"] == second["statusCode"] == 200
    assert first["body"] == second["body"]
    assert len(calls) == 1
    assert stack.table("zone-state").scan()["Items"] == before
    assert stack.table("alerts").scan()["Items"] == []


def test_budget_prevents_provider_call(stack, configured, monkeypatch):
    stack.table("ratelimits").put_item(Item={"k": f"ai-budget#minute#{int(time.time()) // 60}", "n": 5})
    monkeypatch.setattr(configured.briefing, "generate", lambda *args: pytest.fail("provider called over budget"))
    assert request(configured)["statusCode"] == 429


def test_stale_state_and_provider_failure(stack, configured, monkeypatch):
    def fail(*args):
        raise RuntimeError("sensitive provider response")

    monkeypatch.setattr(configured.briefing, "generate", fail)
    response = request(configured)
    assert response["statusCode"] == 503
    assert "sensitive" not in response["body"]
    stack.table("zone-state").update_item(Key={"zone_id": "Z07"},
        UpdateExpression="SET updated_at = :t", ExpressionAttributeValues={":t": int(time.time()) - 301})
    assert request(configured)["statusCode"] == 409
