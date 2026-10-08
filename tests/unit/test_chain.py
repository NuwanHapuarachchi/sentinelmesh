import json

import pytest

from common import chain

BUCKET = "t-decisions"


@pytest.fixture
def env(stack):
    return stack.s3, stack.table("meta")


def record(stack, key, body):
    raw = json.dumps(body).encode()
    stack.s3.put_object(Bucket=BUCKET, Key=key, Body=raw)
    return raw


def write_and_chain(stack, env, n, start=0):
    s3, meta = env
    out = []
    for i in range(start, start + n):
        key = f"decisions/2026/10/09/Z07/r{i}.json"
        out.append(chain.append(s3, BUCKET, meta, key, record(stack, key, {"i": i})))
    return out


def test_records_form_one_linked_chain(stack, env):
    entries = write_and_chain(stack, env, 3)
    assert [e["seq"] for e in entries] == [1, 2, 3]
    first = json.loads(stack.body("DECISIONS_BUCKET", chain.entry_key(1)))
    second = json.loads(stack.body("DECISIONS_BUCKET", chain.entry_key(2)))
    assert first["prev_sha256"] == chain.GENESIS and second["prev_sha256"] == entries[0]["sha256"]
    assert chain.verify(stack.s3, BUCKET) == {"entries": 3, "records": 3, "ok": True, "problems": []}


def test_a_lost_head_hint_is_repaired_from_the_entries(stack, env):
    write_and_chain(stack, env, 2)
    env[1].delete_item(Key={"k": chain.HEAD})  # e.g. a writer died after its entry but before the hint
    [third] = write_and_chain(stack, env, 1, start=2)
    assert third["seq"] == 3
    assert chain.verify(stack.s3, BUCKET)["ok"]


def test_an_edited_record_is_detected(stack, env):
    write_and_chain(stack, env, 3)
    record(stack, "decisions/2026/10/09/Z07/r1.json", {"i": 1, "approved_by": "someone else"})
    report = chain.verify(stack.s3, BUCKET)
    assert not report["ok"] and report["problems"] == ["decisions/2026/10/09/Z07/r1.json: changed after it was recorded (entry 2)"]


def test_a_deleted_record_is_detected(stack, env):
    write_and_chain(stack, env, 3)
    stack.s3.delete_object(Bucket=BUCKET, Key="decisions/2026/10/09/Z07/r2.json")
    assert chain.verify(stack.s3, BUCKET)["problems"] == ["decisions/2026/10/09/Z07/r2.json: deleted (entry 3)"]


def test_a_deleted_chain_entry_is_detected(stack, env):
    write_and_chain(stack, env, 3)
    stack.s3.delete_object(Bucket=BUCKET, Key=chain.entry_key(2))
    problems = chain.verify(stack.s3, BUCKET)["problems"]
    assert any("expected entry 2" in p for p in problems)
    assert any("does not link" in p for p in problems)
    assert "decisions/2026/10/09/Z07/r1.json: not in the chain" in problems


def test_a_record_written_outside_the_chain_is_reported(stack, env):
    write_and_chain(stack, env, 1)
    record(stack, "decisions/2026/10/09/Z07/sneaky.json", {"x": 1})
    assert chain.verify(stack.s3, BUCKET)["problems"] == ["decisions/2026/10/09/Z07/sneaky.json: not in the chain"]


def test_the_issuer_chains_each_decision_once_even_when_retried(stack):
    issuer = stack.load("issuer.handler")
    event = {"op": "issue", "approval": {"decision": "auto"}, "execution": "exec-1", "entered": "2026-10-09T01:00:00Z",
             "input": {"zone_id": "Z07", "tier": "imminent", "hazard": "flood", "origin": "bypass", "trigger_rx_ms": 1}}
    issuer.handler(event, None)
    issuer.handler(event, None)
    issuer.handler({**event, "execution": "exec-2"}, None)
    assert chain.verify(stack.s3, BUCKET) == {"entries": 2, "records": 2, "ok": True, "problems": []}


def test_verify_script_exit_code(stack, env, monkeypatch, capsys):
    from scripts import verify_decisions
    write_and_chain(stack, env, 2)
    assert verify_decisions.main(["--bucket", BUCKET]) == 0
    stack.s3.delete_object(Bucket=BUCKET, Key="decisions/2026/10/09/Z07/r0.json")
    assert verify_decisions.main(["--bucket", BUCKET]) == 1
    assert "CHAIN BROKEN" in capsys.readouterr().out
