import json
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from http.server import ThreadingHTTPServer

import pytest

from common import cap, chain
from edge import local_alert_node as lan
from edge import node_core

ZONE = {"zone_id": "Z35", "name": "Kegalle 03", "district": "Kegalle", "hazard": "flood", "warning_level_m": 3.0,
        "danger_level_m": 4.5, "gauge_cadence_s": 60,
        "polygon": [[80.3, 7.2], [80.31, 7.21], [80.32, 7.2], [80.31, 7.19], [80.3, 7.2]]}
OTHER = {**ZONE, "zone_id": "Z36", "name": "Kegalle 04"}
BUNDLE = {"district": "Kegalle", "slug": "kegalle", "version": "v1", "generated_at": 1000, "zones": [ZONE, OTHER],
          "recipients": [{"subscriber_id": "s1", "zone_id": "Z35", "lang": "si", "msisdn": "+94770000001"},
                         {"subscriber_id": "s2", "zone_id": "Z35", "lang": "en"},
                         {"subscriber_id": "s3", "zone_id": "Z36", "lang": "ta", "msisdn": "+94770000003"}]}
CFG = {"thing": "sentinelmesh-node-kegalle", "slug": "kegalle", "evidence_bucket": "t-evidence",
       "decisions_bucket": "t-decisions", "meta_table": "t-meta"}


class Clock:
    def __init__(self, t=10_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def node(tmp_path):
    sent = []
    clock = Clock()
    n = lan.Node(CFG, gateway_env={"SMS_HTTP_URL": "http://192.168.43.1:8082/send"}, pin="1234", state_dir=tmp_path,
                 sender=lambda to, text, ref, secret, env: sent.append((to, text)) or {"status": "SENT"}, clock=clock)
    n.store.update(json.dumps(BUNDLE))
    n.sent, n.test_clock = sent, clock
    return n


# ---- decision rules ----

def test_below_danger_is_nothing_and_cancels_a_pending_zone():
    d = node_core.Decider(grace_s=60)
    assert d.reading(ZONE, 4.6, 0, cloud_alive=True) == "wait"
    assert d.reading(ZONE, 4.0, 10, cloud_alive=True) == "below"
    assert d.pending == {}


def test_cloud_gone_means_issue_now():
    assert node_core.Decider().reading(ZONE, 4.6, 0, cloud_alive=False) == "issue"


def test_cloud_alive_means_wait_for_the_grace_then_issue():
    d = node_core.Decider(grace_s=60)
    assert d.reading(ZONE, 4.6, 0, True) == "wait"
    assert d.due(30, True) == []
    assert d.due(61, True) == ["Z35"]


def test_a_cloud_alert_within_the_grace_stands_the_node_down():
    d = node_core.Decider(grace_s=60)
    d.reading(ZONE, 4.6, 0, True)
    d.cloud_alert("Z35", "imminent", 20)
    assert d.due(61, True) == [] and d.reading(ZONE, 4.8, 70, True) == "covered"


def test_a_lower_cloud_alert_does_not_cover_an_imminent_reading():
    d = node_core.Decider(grace_s=60)
    d.cloud_alert("Z35", "warning", 0)
    assert d.reading(ZONE, 4.6, 1, False) == "issue"


def test_one_local_alert_per_zone_per_six_hours():
    d = node_core.Decider()
    d.issued("Z35", 0)
    assert d.reading(ZONE, 4.9, 3600, False) == "already"
    assert d.reading(ZONE, 4.9, 6 * 3600 + 1, False) == "issue"


# ---- bundle and log ----

def test_bundle_survives_a_restart_and_rejects_junk(tmp_path):
    store = node_core.BundleStore(tmp_path / "bundle.json")
    assert store.bundle is None and store.zone("Z35") is None
    assert store.update(json.dumps(BUNDLE)) is True and store.update(json.dumps(BUNDLE)) is False
    again = node_core.BundleStore(tmp_path / "bundle.json")
    assert again.zone("Z35")["danger_level_m"] == 4.5 and len(again.recipients("Z35")) == 2
    with pytest.raises(ValueError):
        again.update('{"zones": []}')
    assert again.bundle["version"] == "v1"


def test_local_log_is_hash_chained_and_tamper_evident(tmp_path):
    log = node_core.LocalLog(tmp_path / "decisions.jsonl")
    for i in range(3):
        log.append({"i": i})
    assert log.verify() == []
    lines = log.lines()
    lines[1] = lines[1].replace('"i": 1', '"i": 99')
    (tmp_path / "decisions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert log.verify() == ["line 3: does not link to the line before"]


# ---- the node ----

def test_with_the_cloud_gone_a_danger_reading_is_issued_at_once(node):
    assert node.on_reading({"zone_id": "Z35", "source_class": "gauge", "level_m": 4.9}) == "issue"
    assert node.sent == [("+94770000001", node.broadcasts["Z35"]["texts"]["si"])]  # only the real number gets SMS
    b = node.broadcasts["Z35"]
    assert b["tier"] == "imminent" and b["status"] == "Exercise" and b["alert_id"].startswith("LOCAL-kegalle-Z35-")
    [entry] = node.log.pending()
    record = entry["record"]
    assert record["sms_sent"] == 1 and record["recipients"] == 2 and record["cloud_alive"] is False
    root = ET.fromstring(record["cap_xml"])
    assert root.find(f"{{{cap.NS}}}status").text == "Exercise"
    assert node.on_reading({"zone_id": "Z35", "level_m": 5.0}) == "already"


def test_with_the_cloud_alive_the_node_defers_then_acts_alone(node):
    node.last_cloud_contact = node.test_clock.t
    assert node.on_reading({"zone_id": "Z35", "level_m": 4.9}) == "wait"
    assert node.on_reading({"zone_id": "Z36", "level_m": 4.9}) == "wait"
    node.on_cloud_alert({"zone_id": "Z35", "tier": "imminent", "alert_id": "SM-1"})  # the cloud warned Z35 in time
    node.test_clock.t += 61
    node.last_cloud_contact = node.test_clock.t
    node.tick()
    assert set(node.broadcasts) == {"Z36"}
    assert [s[0] for s in node.sent] == ["+94770000003"]


def test_junk_readings_are_refused(node):
    assert node.on_reading({"zone_id": "Z35", "level_m": "<script>"}) == "invalid"
    assert node.on_reading({"zone_id": "Z35", "level_m": True}) == "invalid"
    assert node.on_reading({"zone_id": "Z99", "level_m": 9.0}) == "unknown zone"
    assert node.on_reading({"zone_id": "Z35", "source_class": "rain", "rain_24h_mm": 300}) == "ignored"
    assert node.broadcasts == {}


# ---- LAN HTTP service ----

@pytest.fixture
def server(node):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), lan.make_handler(node))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def http(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_field_station_reading_over_the_lan_reaches_phones_on_the_lan(node, server):
    status, body = http("POST", f"{server}/reading", {"zone_id": "Z35", "source_class": "gauge", "level_m": 4.9})
    assert status == 200 and json.loads(body) == {"decision": "issue"}
    status, body = http("GET", f"{server}/cb/Z35.json")
    assert status == 200 and json.loads(body)["tier"] == "imminent" and "cap_xml" not in json.loads(body)
    assert http("GET", f"{server}/cb/Z36.json")[0] == 404
    status, page = http("GET", f"{server}/phone.html")
    assert status == 200 and b"phone.js" in page
    assert json.loads(http("GET", f"{server}/config.json")[1]) == {"api_url": ""}


def test_manual_issue_needs_the_officer_pin(node, server):
    assert http("POST", f"{server}/issue", {"zone_id": "Z36"})[0] == 403
    status, body = http("POST", f"{server}/issue", {"zone_id": "Z36", "tier": "warning"}, {"X-Officer-Pin": "1234"})
    assert status == 200 and json.loads(body)["sms_sent"] == 1
    assert http("POST", f"{server}/issue", {"zone_id": "Z99"}, {"X-Officer-Pin": "1234"})[0] == 400


def test_status_page_and_document(node, server):
    node.on_reading({"zone_id": "Z35", "level_m": 4.9})
    status = json.loads(http("GET", f"{server}/status")[1])
    assert status["district"] == "Kegalle" and status["cloud_alive"] is False and status["issued"] == ["Z35"]
    assert status["log_ok"] is True
    page = http("GET", f"{server}/")[1].decode()
    assert "Kegalle Local Alert Node" in page and "UNREACHABLE" in page


# ---- uploading into the cloud chain ----

def test_local_decisions_join_the_cloud_decision_chain(stack, node):
    node.on_reading({"zone_id": "Z35", "level_m": 4.9})
    node.on_reading({"zone_id": "Z36", "level_m": 4.9})
    assert node.upload(stack.s3, stack.table("meta")) == 2
    assert node.upload(stack.s3, stack.table("meta")) == 0  # nothing left to send
    keys = stack.decision_records()
    assert len(keys) == 2 and all(k.startswith("decisions/edge/sentinelmesh-node-kegalle/") for k in keys)
    assert chain.verify(stack.s3, "t-decisions")["ok"]


def test_an_upload_interrupted_before_chaining_is_completed_on_retry(stack, node):
    node.on_reading({"zone_id": "Z35", "level_m": 4.9})
    [entry] = node.log.pending()
    key = f"decisions/edge/{CFG['thing']}/{entry['seq']:06d}-{entry['record']['alert_id']}.json"
    stack.s3.put_object(Bucket="t-decisions", Key=key, Body=json.dumps(entry, sort_keys=True, ensure_ascii=False).encode())
    assert node.upload(stack.s3, stack.table("meta")) == 1
    assert chain.verify(stack.s3, "t-decisions")["ok"]


def test_credentials_provider_response():
    body = json.dumps({"credentials": {"accessKeyId": "AK", "secretAccessKey": "SK", "sessionToken": "ST", "expiration": "x"}})
    assert lan.parse_credentials(body) == {"aws_access_key_id": "AK", "aws_secret_access_key": "SK", "aws_session_token": "ST"}
