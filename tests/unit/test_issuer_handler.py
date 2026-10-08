import json
import xml.etree.ElementTree as ET

import pytest

from common import cap


@pytest.fixture
def issuer(stack):
    return stack.load("issuer.handler")


def bypass_input(zone_id, **extra):
    """What the IoT hard-threshold rule starts the workflow with: no is_test, no approval fields."""
    return {"zone_id": zone_id, "tier": "imminent", "hazard": "flood", "reason": "hard_threshold", "origin": "bypass",
            "cell": "a", "score": 1.0, "level_m": 9.0, "source_id": "heartbeat", "trigger_rx_ms": 1_000, **extra}


def issue(issuer, inp, approval=None):
    return issuer.handler({"op": "issue", "input": inp, "approval": approval or {"decision": "auto"}}, None)


def alert_item(stack, alert_id):
    return stack.table("alerts").get_item(Key={"alert_id": alert_id, "sk": "META"})["Item"]


def test_bypass_alert_for_a_test_zone_is_a_test(stack, issuer):
    out = issue(issuer, bypass_input("H00"))
    assert out["is_test"] is True
    assert alert_item(stack, out["alert_id"])["status"] == "Test"
    assert json.loads(out["message"])["status"] == "Test"
    root = ET.fromstring(stack.body("WEB_BUCKET", alert_item(stack, out["alert_id"])["cap_key"]))
    assert root.find(f"{{{cap.NS}}}status").text == "Test"


@pytest.mark.parametrize(("extra", "status"), [({}, "Actual"), ({"drill": True}, "Exercise"), ({"is_test": True}, "Test")])
def test_status_for_a_real_zone(stack, issuer, extra, status):
    out = issue(issuer, bypass_input("Z07", **extra))
    assert alert_item(stack, out["alert_id"])["status"] == status
    assert out["is_test"] is (status == "Test")


def test_issue_writes_cap_alert_decision_and_queue_message(stack, issuer):
    out = issue(issuer, bypass_input("Z07"))
    assert out["queue_url"] == stack.queues["imminent"]
    message = json.loads(out["message"])
    assert set(message["texts"]) == {"en", "si", "ta"} and message["tier"] == "imminent"
    assert stack.keys("WEB_BUCKET", "cap/") == [alert_item(stack, out["alert_id"])["cap_key"]]
    [key] = stack.keys("DECISIONS_BUCKET", "decisions/")
    decision = json.loads(stack.body("DECISIONS_BUCKET", key))
    assert decision["alert_id"] == out["alert_id"] and decision["mode"] == "auto"
    assert decision["rules_version"] == "test-sha"


def test_warning_timeout_marks_the_approval_auto_issued(stack, issuer):
    stack.table("approvals").put_item(Item={"approval_id": "X1", "status": "PENDING", "pending": "PENDING", "created_at": 1})
    inp = {**bypass_input("Z07"), "tier": "warning", "origin": "scorer"}
    out = issue(issuer, inp, approval={"decision": "timeout", "approval_id": "X1"})
    assert alert_item(stack, out["alert_id"])["mode"] == "timeout"
    item = stack.table("approvals").get_item(Key={"approval_id": "X1"})["Item"]
    assert item["status"] == "AUTO_ISSUED" and "pending" not in item


def record(issuer, mode, approval_id="X1"):
    inp = {**bypass_input("Z07"), "tier": "warning", "origin": "scorer"}
    return issuer.handler({"op": "record", "mode": mode, "input": inp,
                           "approval": {"decision": "timeout", "approval_id": approval_id}}, None)


@pytest.mark.parametrize("before", ["PENDING", "DECIDING", "APPROVED"])
def test_superseded_warning_is_recorded_and_settles_the_approval(stack, issuer, before):
    stack.table("approvals").put_item(Item={"approval_id": "X1", "status": before, "pending": "PENDING", "created_at": 1})
    record(issuer, "superseded")
    item = stack.table("approvals").get_item(Key={"approval_id": "X1"})["Item"]
    assert item["status"] == "SUPERSEDED" and "pending" not in item
    [key] = stack.keys("DECISIONS_BUCKET", "decisions/")
    decision = json.loads(stack.body("DECISIONS_BUCKET", key))
    assert decision["mode"] == "superseded" and decision["alert_id"] is None
    assert stack.table("alerts").scan()["Items"] == []
    assert stack.keys("WEB_BUCKET", "cap/") == []


def test_superseded_does_not_overwrite_a_rejection(stack, issuer):
    stack.table("approvals").put_item(Item={"approval_id": "X1", "status": "REJECTED", "created_at": 1})
    record(issuer, "superseded")
    assert stack.table("approvals").get_item(Key={"approval_id": "X1"})["Item"]["status"] == "REJECTED"


def test_rejected_record_leaves_the_approval_alone(stack, issuer):
    stack.table("approvals").put_item(Item={"approval_id": "X1", "status": "REJECTED", "created_at": 1})
    record(issuer, "rejected")
    assert stack.table("approvals").get_item(Key={"approval_id": "X1"})["Item"]["status"] == "REJECTED"
    [key] = stack.keys("DECISIONS_BUCKET", "decisions/")
    assert json.loads(stack.body("DECISIONS_BUCKET", key))["mode"] == "rejected"


def test_unknown_record_mode_is_an_error(stack, issuer):
    with pytest.raises(ValueError):
        record(issuer, "deleted")
