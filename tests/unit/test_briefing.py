import json

import pytest

from common import briefing


def fixture():
    return briefing.snapshot({"danger_level_m": 5}, {"updated_at": 999, "doc": json.dumps({
        "ev": {"gauge": {"value": 3, "ts": 990, "src": "private"}},
        "reports": [{"phone": "private"}], "gauge_hist": [[800, 1], [900, 2], [990, 3]],
    })}, 1000)


def output():
    return {s: [{"text": "Review the supplied gauge evidence.", "evidence": ["gauge"]}] for s in briefing.SECTIONS}


def test_snapshot_removes_personal_fields_and_uses_recent_history():
    data = fixture()
    assert "private" not in json.dumps(data)
    assert data["history_sufficient"]
    assert data["facts"]["gauge"]["age_seconds"] == 10


@pytest.mark.parametrize("field,value", [("text", "Flood probability is 90%"), ("evidence", ["invented"]), ("text", "")])
def test_rejects_unsupported_claims(field, value):
    result = output()
    result["insights"][0][field] = value
    with pytest.raises(ValueError):
        briefing.validate(result, fixture())


def test_missing_history_forces_insufficient_data():
    data = fixture()
    data["history_sufficient"] = False
    assert "Insufficient" in briefing.validate(output(), data)["outlook"][0]["text"]


def test_provider_uses_header_key_and_validates_response(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return json.dumps({"candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": json.dumps(output())}]}}]}).encode()

    def invoke(req, timeout):
        assert "secret" not in req.full_url
        assert req.headers["X-goog-api-key"] == "secret"
        assert timeout == 10
        assert "private" not in req.data.decode()
        return Response()

    monkeypatch.setattr(briefing, "urlopen", invoke)
    assert briefing.generate(fixture(), "secret", "gemini-test")["insights"]
