import json

import pytest
from botocore.exceptions import ClientError
from conftest import api_event


class FakeSfn:
    """Records task callbacks; optionally fails like an expired token."""

    def __init__(self, error=None):
        self.calls, self.error = [], error

    def send_task_success(self, taskToken, output):
        if self.error:
            raise ClientError({"Error": {"Code": self.error, "Message": "x"}}, "SendTaskSuccess")
        self.calls.append((taskToken, json.loads(output)))


@pytest.fixture
def approvals(stack):
    mod = stack.load("approvals.handler")
    mod.sfn = FakeSfn()
    return mod


def request(mod, approval_id="Z07-warning-e1-100", zone="Z07", is_test=False):
    return mod.handler({
        "op": "approval_request", "token": "tok-" + approval_id, "approval_id": approval_id,
        "input": {"zone_id": zone, "tier": "warning", "hazard": "flood", "approval_timeout_s": 300, "score": 0.6,
                  "agreeing": ["gauge", "forecast"], "is_test": is_test, "cell": "a"},
    }, None)


def decide(mod, approval_id, decision, note="", groups="duty_officer"):
    event = api_event("POST", "/approvals/{id}", {"decision": decision, "note": note}, groups=groups, path={"id": approval_id})
    resp = mod.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"])


def test_request_stores_pending_approval_and_hides_token(stack, approvals):
    request(approvals)
    resp = approvals.handler(api_event("GET", "/approvals", groups="duty_officer"), None)
    items = json.loads(resp["body"])["approvals"]
    assert [i["approval_id"] for i in items] == ["Z07-warning-e1-100"]
    assert "task_token" not in items[0]
    assert 0 < items[0]["seconds_left"] <= 300


def test_viewer_cannot_list_or_decide(stack, approvals):
    assert approvals.handler(api_event("GET", "/approvals", groups="viewer"), None)["statusCode"] == 403
    assert decide(approvals, "x", "approve", groups="viewer")[0] == 403


def test_approve_resumes_workflow_with_officer_identity(stack, approvals):
    request(approvals)
    status, body = decide(approvals, "Z07-warning-e1-100", "approve", note="looks right")
    assert status == 200 and body["decision"] == "approve"
    token, output = approvals.sfn.calls[0]
    assert token == "tok-Z07-warning-e1-100"
    assert output["decided_by"] == "officer@test" and output["decision"] == "approve"
    item = stack.table("approvals").get_item(Key={"approval_id": "Z07-warning-e1-100"})["Item"]
    assert item["status"] == "APPROVED" and "pending" not in item


def test_reject_needs_a_note(stack, approvals):
    request(approvals)
    assert decide(approvals, "Z07-warning-e1-100", "reject")[0] == 400


def test_second_decision_is_refused(stack, approvals):
    request(approvals)
    assert decide(approvals, "Z07-warning-e1-100", "approve")[0] == 200
    assert decide(approvals, "Z07-warning-e1-100", "reject", note="late")[0] == 409
    assert len(approvals.sfn.calls) == 1


def test_expired_token_marks_auto_issued(stack, approvals):
    request(approvals)
    approvals.sfn = FakeSfn(error="TaskTimedOut")
    status, _ = decide(approvals, "Z07-warning-e1-100", "approve")
    assert status == 409
    item = stack.table("approvals").get_item(Key={"approval_id": "Z07-warning-e1-100"})["Item"]
    assert item["status"] == "AUTO_ISSUED"


class SupersedingSfn(FakeSfn):
    """The workflow supersedes the warning (issuer settles it) while the officer's approval is in flight."""

    def __init__(self, table, approval_id, error=None):
        super().__init__(error)
        self.table, self.approval_id = table, approval_id

    def send_task_success(self, taskToken, output):
        self.table.update_item(Key={"approval_id": self.approval_id}, UpdateExpression="SET #s = :s",
                               ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": "SUPERSEDED"})
        super().send_task_success(taskToken, output)


def test_late_approval_does_not_overwrite_superseded(stack, approvals):
    request(approvals)
    approvals.sfn = SupersedingSfn(stack.table("approvals"), "Z07-warning-e1-100")
    status, body = decide(approvals, "Z07-warning-e1-100", "approve")
    assert status == 200 and body["status"] == "SUPERSEDED"
    assert stack.table("approvals").get_item(Key={"approval_id": "Z07-warning-e1-100"})["Item"]["status"] == "SUPERSEDED"


def test_expired_and_superseded_reports_superseded(stack, approvals):
    request(approvals)
    approvals.sfn = SupersedingSfn(stack.table("approvals"), "Z07-warning-e1-100", error="TaskTimedOut")
    status, body = decide(approvals, "Z07-warning-e1-100", "approve")
    assert status == 409 and body["status"] == "SUPERSEDED"


def test_all_clear_requests_are_listed_like_warnings(stack, approvals):
    approvals.handler({
        "op": "approval_request", "token": "tok-c", "approval_id": "Z07-clear-e1-1",
        "input": {"zone_id": "Z07", "tier": "clear", "hazard": "flood", "approval_timeout_s": 600, "score": 0.1},
    }, None)
    items = json.loads(approvals.handler(api_event("GET", "/approvals", groups="duty_officer"), None)["body"])["approvals"]
    assert [(i["approval_id"], i["tier"]) for i in items] == [("Z07-clear-e1-1", "clear")]
