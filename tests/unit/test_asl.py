"""Structural checks on the escalation workflow (terraform/modules/cell/escalation.asl.json).

The template is rendered with dummy values, parsed, and walked: every transition must name a real
state, every state must be reachable, and the safety-relevant paths are asserted explicitly.
"""

import json
import re
from pathlib import Path

import pytest

ASL = Path(__file__).resolve().parents[2] / "terraform" / "modules" / "cell" / "escalation.asl.json"


@pytest.fixture(scope="module")
def machine():
    text = re.sub(r"\$\{(\w+)\}", lambda m: f"dummy-{m.group(1)}", ASL.read_text(encoding="utf-8"))
    return json.loads(text)


def targets(state):
    out = []
    if "Next" in state:
        out.append(state["Next"])
    if "Default" in state:
        out.append(state["Default"])
    out += [c["Next"] for c in state.get("Choices", [])]
    out += [c["Next"] for c in state.get("Catch", [])]
    return out


def test_every_transition_names_a_state(machine):
    states = machine["States"]
    assert machine["StartAt"] in states
    for name, state in states.items():
        for t in targets(state):
            assert t in states, f"{name} -> {t}"


def test_every_state_is_reachable_and_terminates(machine):
    states, seen, todo = machine["States"], set(), [machine["StartAt"]]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo += targets(states[name])
    assert seen == set(states), f"unreachable: {set(states) - seen}"
    for name, state in states.items():
        terminal = state["Type"] in ("Succeed", "Fail") or state.get("End") is True
        assert terminal or targets(state), f"{name} has no way out"


def test_test_alerts_skip_the_officer_email(machine):
    states = machine["States"]
    assert states["Issue"]["ResultSelector"]["is_test.$"] == "$.Payload.is_test"
    assert states["Enqueue"]["Next"] == "IsTestAlert"
    choice = states["IsTestAlert"]
    assert choice["Choices"] == [{"Variable": "$.issue.is_test", "BooleanEquals": True, "Next": "Done"}]
    assert choice["Default"] == "NotifyOfficers"


def reachable(states, start, blocked=()):
    seen, todo = set(), [start]
    while todo:
        name = todo.pop()
        if name in seen or name in blocked:
            continue
        seen.add(name)
        todo += targets(states[name])
    return seen


def test_a_warning_that_waited_for_an_officer_is_rechecked_before_issuing(machine):
    states = machine["States"]
    assert states["MarkTimeout"]["Next"] == "StillCurrent"
    assert states["Decision"]["Default"] == "StillCurrent"
    # No route from the approval wait reaches Issue except through the ownership check.
    assert "Issue" not in reachable(states, "RequestApproval", blocked={"StillCurrent"})
    check = states["StillCurrent"]
    assert check["Parameters"]["ConditionExpression"] == "alert_exec = :e"
    assert check["Parameters"]["ExpressionAttributeValues"][":e"] == {"S.$": "$$.Execution.Name"}
    catches = {c["ErrorEquals"][0]: c["Next"] for c in check["Catch"]}
    assert catches == {"DynamoDB.ConditionalCheckFailedException": "RecordSuperseded", "States.ALL": "Issue"}


def test_a_superseded_warning_is_recorded_and_keeps_the_newer_gate(machine):
    states = machine["States"]
    payload = states["RecordSuperseded"]["Parameters"]["Payload"]
    assert payload["op"] == "record" and payload["mode"] == "superseded"
    after = reachable(states, "RecordSuperseded")
    assert after == {"RecordSuperseded", "Superseded"}
    assert states["Superseded"]["Type"] == "Succeed"
    releases = {n for n, s in states.items() if s.get("Parameters", {}).get("UpdateExpression", "").startswith("REMOVE alert_rank")}
    assert releases == {"ReleaseGate", "ReleaseAfterFailure"} and not releases & after


def test_gate_and_releases_clear_the_confirmation_marker(machine):
    states = machine["States"]
    assert states["Gate"]["Parameters"]["UpdateExpression"].endswith("REMOVE alert_confirmed_at")
    for name in ("ReleaseGate", "ReleaseAfterFailure"):
        assert "alert_confirmed_at" in states[name]["Parameters"]["UpdateExpression"]
    assert states["RecordReject"]["Parameters"]["Payload"]["mode"] == "rejected"


def test_issue_and_records_carry_execution_identity_for_idempotency(machine):
    states = machine["States"]
    for name in ("Issue", "RecordReject", "RecordSuperseded"):
        payload = states[name]["Parameters"]["Payload"]
        assert payload["execution.$"] == "$$.Execution.Name", name
        assert payload["entered.$"] == "$$.State.EnteredTime", name


def test_queue_failure_tells_officers_and_frees_the_gate(machine):
    states = machine["States"]
    [catch] = states["Enqueue"]["Catch"]
    assert catch["ErrorEquals"] == ["States.ALL"] and catch["Next"] == "EnqueueFailed"
    assert states["EnqueueFailed"]["Resource"] == "arn:aws:states:::sns:publish"
    # Officer notifications must never stand between a failure and releasing the gate.
    for name in ("IssueFailed", "EnqueueFailed"):
        assert states[name]["Next"] == "ReleaseAfterFailure"
        assert [c["Next"] for c in states[name]["Catch"]] == ["ReleaseAfterFailure"]
    assert states["ReleaseAfterFailure"]["Next"] == "Failed"
