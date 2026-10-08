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
