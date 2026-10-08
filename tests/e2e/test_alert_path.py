"""The proof moments, against the live stack:

- one source alone -> watch, no alert
- gauge + forecast + reports agree -> warning -> officer approves -> phone inbox
- nobody approves -> the alert is issued anyway (fail toward alerting)
- scorer switched off -> the IoT hard-threshold bypass still issues imminent
- gauges fall silent after rising -> zone marked blind and escalated
- failover is a data change -> the next reading lands in the other cell
"""

import json
import time

from boto3.dynamodb.conditions import Key
from conftest import alerts_for, table, wait_for


def zone_cfg(aws, zone_id):
    return table(aws, "zones").get_item(Key={"zone_id": zone_id})["Item"]


def state(aws, zone_id):
    return table(aws, "zone-state").get_item(Key={"zone_id": zone_id}, ConsistentRead=True).get("Item") or {}


def gauge_level(aws, zone_id, severity):
    z = zone_cfg(aws, zone_id)
    w, d = float(z["warning_level_m"]), float(z["danger_level_m"])
    return round(w + (severity - 0.6) / 0.4 * (d - w), 2)


def report(publish, zone_id, n):
    for i in range(n):
        publish(zone_id, "report", reporter_hash=f"e2e-reporter-{i}", report_kind="flooding", report_sev=3)


def test_single_source_is_only_watch(aws, publish, reset_zone):
    reset_zone("T01", owner="a")
    since = time.time()
    publish("T01", "gauge", level_m=gauge_level(aws, "T01", 0.8))
    wait_for(lambda: state(aws, "T01").get("tier") == "watch")
    time.sleep(5)
    assert alerts_for(aws, "T01", since) == []


def test_corroborated_warning_approved_reaches_phone(aws, publish, reset_zone, subscriber):
    reset_zone("T01", owner="a")
    sid = subscriber("T01", lang="si")
    since = time.time()
    publish("T01", "gauge", level_m=gauge_level(aws, "T01", 0.8))
    publish("T01", "forecast", fc_rain_24h_mm=90)
    report(publish, "T01", 2)
    wait_for(lambda: state(aws, "T01").get("tier") == "warning")

    approvals = table(aws, "approvals")
    pending = wait_for(lambda: [
        a for a in approvals.query(IndexName="pending", KeyConditionExpression=Key("pending").eq("PENDING"))["Items"]
        if a["zone_id"] == "T01"
    ])
    event = {
        "httpMethod": "POST",
        "pathParameters": {"id": pending[0]["approval_id"]},
        "body": json.dumps({"decision": "approve", "note": "e2e"}),
        "requestContext": {"authorizer": {"claims": {"cognito:groups": "duty_officer", "email": "e2e@test"}}},
    }
    resp = aws["lambda"].invoke(FunctionName="sentinelmesh-approvals", Payload=json.dumps(event))
    assert json.loads(json.loads(resp["Payload"].read())["body"])["decision"] == "approve"

    # Readings arrive one by one, so the zone may pass through advisory first; take the warning alert.
    alert = wait_for(lambda: [a for a in alerts_for(aws, "T01", since) if a["tier"] == "warning"])[0]
    assert alert["mode"] == "approve" and alert["status"] == "Test"
    msg = wait_for(lambda: [m for m in table(aws, "inbox").query(KeyConditionExpression=Key("subscriber_id").eq(sid))["Items"]
                            if m["alert_id"] == alert["alert_id"]])[0]
    assert msg["lang"] == "si"
    # CAP 1.2 file published for the alert
    aws["s3"].head_object(Bucket=f"sentinelmesh-web-{aws['s'].client('sts').get_caller_identity()['Account']}",
                          Key=alert["cap_key"])


def test_unanswered_warning_is_issued_after_timeout(aws, publish, reset_zone):
    reset_zone("T02", owner="a")
    since = time.time()
    publish("T02", "gauge", level_m=gauge_level(aws, "T02", 0.8))
    publish("T02", "forecast", fc_rain_24h_mm=90)
    report(publish, "T02", 2)
    alert = wait_for(lambda: [a for a in alerts_for(aws, "T02", since) if a["tier"] == "warning"], timeout=120)[0]
    assert alert["mode"] == "timeout"


def test_waiting_warning_is_superseded_by_imminent(aws, publish, reset_zone):
    # A warning waits for an officer; meanwhile the river passes the danger level and imminent is issued.
    # When the approval window closes, the stale warning must not go out after the emergency.
    reset_zone("T01", owner="a")  # T01's approval window is 60 s
    since = time.time()
    publish("T01", "gauge", level_m=gauge_level(aws, "T01", 0.8))
    publish("T01", "forecast", fc_rain_24h_mm=90)
    report(publish, "T01", 2)
    wait_for(lambda: state(aws, "T01").get("tier") == "warning")
    approvals = table(aws, "approvals")
    pending = wait_for(lambda: [
        a for a in approvals.query(IndexName="pending", KeyConditionExpression=Key("pending").eq("PENDING"))["Items"]
        if a["zone_id"] == "T01"
    ])[0]

    publish("T01", "gauge", level_m=float(zone_cfg(aws, "T01")["danger_level_m"]) + 0.2)
    wait_for(lambda: [a for a in alerts_for(aws, "T01", since) if a["tier"] == "imminent"])

    settled = wait_for(lambda: (i := approvals.get_item(Key={"approval_id": pending["approval_id"]})["Item"])["status"] != "PENDING"
                       and i, timeout=120)
    assert settled["status"] == "SUPERSEDED"
    time.sleep(5)
    assert not [a for a in alerts_for(aws, "T01", since) if a["tier"] == "warning"]


def test_bypass_issues_imminent_when_scorer_is_off(aws, publish, reset_zone, outputs):
    reset_zone("T03", owner="b")
    lam = aws["lambda"]
    mapping = outputs["cells"]["b"]["scorer_mapping"]
    lam.update_event_source_mapping(UUID=mapping, Enabled=False)
    try:
        wait_for(lambda: lam.get_event_source_mapping(UUID=mapping)["State"] == "Disabled", timeout=120)
        since = time.time()
        danger = float(zone_cfg(aws, "T03")["danger_level_m"])
        publish("T03", "gauge", level_m=danger + 0.2)
        alert = wait_for(lambda: alerts_for(aws, "T03", since), timeout=90)[0]
        assert alert["tier"] == "imminent"
        assert alert["origin"] == "bypass"
    finally:
        lam.update_event_source_mapping(UUID=mapping, Enabled=True)


def test_bypass_alert_in_a_test_zone_is_never_actual(aws, publish, reset_zone):
    # The bypass input has no is_test flag; the issuer must still label a test zone's alert "Test"
    # (this is the heartbeat's path, which used to go out as "Actual").
    reset_zone("T03", owner="b")
    since = time.time()
    danger = float(zone_cfg(aws, "T03")["danger_level_m"])
    publish("T03", "gauge", level_m=danger + 0.2, drill=False)
    alert = wait_for(lambda: alerts_for(aws, "T03", since), timeout=90)[0]
    assert alert["tier"] == "imminent"
    assert alert["status"] == "Test"


def test_silence_after_rising_marks_zone_blind(aws, publish, reset_zone):
    reset_zone("T04", owner="b")
    base = gauge_level(aws, "T04", 0.62)
    for i in range(4):
        publish("T04", "gauge", level_m=round(base + 0.15 * i, 2), ts=time.time() - 60 + 15 * i)
        time.sleep(1)
    wait_for(lambda: state(aws, "T04").get("tier") in ("watch", "advisory", "warning"))
    # T04's gauge cadence is 20 s: blind after 60 s of silence, caught by the 1-minute watchdog.
    st = wait_for(lambda: (s := state(aws, "T04")).get("blind") and s, timeout=180, every=10)
    assert st["tier"] == "warning"
    assert json.loads(st["doc"])["reason"] == "blind_escalation"


def test_failover_is_a_data_change(aws, publish, reset_zone):
    reset_zone("T01", owner="b")  # move T01 from cell a to cell b; reset_zone restores it afterwards
    since_ms = int(time.time() * 1000)
    publish("T01", "gauge", level_m=1.0)

    def landed_in_b():
        items = aws["ddb"].Table("sentinelmesh-b-observations").query(
            KeyConditionExpression=Key("zone_id").eq("T01") & Key("sk").gt(str(since_ms))
        )["Items"]
        return items

    assert wait_for(landed_in_b)
    wait_for(lambda: state(aws, "T01").get("eval_cell") == "b")
    table(aws, "zones").update_item(Key={"zone_id": "T01"}, UpdateExpression="SET owner_cell = :c",
                                    ExpressionAttributeValues={":c": "a"})
