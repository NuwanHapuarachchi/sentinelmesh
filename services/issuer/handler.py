"""Issuer: called by the escalation workflow.

op=issue  -> alert id, CAP 1.2 file, alert record, decision record, message for the tier queue.
             tier=clear is an all-clear: a CAP Update (AllClear) referencing the zone's last alert, sent on
             the advisory queue. Any other tier is recorded as the zone's last alert, which a later
             all-clear will reference.
op=record -> decision record only, for a warning or all-clear that was not issued:
             mode=rejected    (an officer held it back)
             mode=superseded  (a higher tier was issued, or the zone fell below advisory / rose again, while it waited)
             mode=unconfirmed (nobody confirmed an all-clear in time, so the earlier alert stays in force)

Decision records are write-once (the bucket policy rejects any PutObject without If-None-Match) and each is
linked into a hash chain (common/chain.py), so an edited or deleted record shows up in verification.
Environment: ALERTS_TABLE, APPROVALS_TABLE, ZONES_TABLE, STATE_TABLE, WEB_BUCKET, DECISIONS_BUCKET, CELL,
QUEUE_IMMINENT, QUEUE_WARNING, QUEUE_ADVISORY, RULES_VERSION, META_TABLE (chain head), PUBLIC_FEED_FUNCTION and
IOT_ENDPOINT (optional: tells the district's Local Alert Node about each alert on sm/alerts/<district>).
"""

import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

from common import cap, chain, messages
from common.districts import slug
from common.observe import log, metric

ddb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
lam = boto3.client("lambda")
iot = boto3.client("iot-data", endpoint_url=f"https://{os.environ['IOT_ENDPOINT']}") if os.environ.get("IOT_ENDPOINT") else None
alerts = ddb.Table(os.environ["ALERTS_TABLE"])
approvals = ddb.Table(os.environ["APPROVALS_TABLE"])
zones = ddb.Table(os.environ["ZONES_TABLE"])
states = ddb.Table(os.environ["STATE_TABLE"])
meta = ddb.Table(os.environ["META_TABLE"]) if os.environ.get("META_TABLE") else None
QUEUES = {t: os.environ[f"QUEUE_{t.upper()}"] for t in ("imminent", "warning", "advisory")}
QUEUES["clear"] = QUEUES["advisory"]  # an all-clear is not urgent
# Approval states the workflow may still move an approval out of (an officer may be mid-decision).
OPEN = ("PENDING", "DECIDING")


def handler(event, context):
    if event.get("op") == "record":
        mode = event.get("mode", "rejected")
        if mode not in ("rejected", "superseded", "unconfirmed"):
            raise ValueError(f"unknown record mode {mode!r}")
        approval = event.get("approval") or {}
        if mode == "superseded":
            # An approved warning can still be superseded before it issues, so APPROVED moves too.
            settle_approval(approval.get("approval_id"), "SUPERSEDED", (*OPEN, "APPROVED"))
        elif mode == "unconfirmed":
            settle_approval(approval.get("approval_id"), "EXPIRED", OPEN)
        return record_decision(event["input"], mode=mode, approval=approval, execution=event.get("execution"),
                               at=epoch(event.get("entered")))
    return issue(event["input"], event.get("approval") or {}, event.get("execution"), event.get("entered"))


def epoch(iso_time):
    """Step Functions context timestamps ('2026-10-08T06:30:00.123Z') to epoch seconds; None if absent."""
    return datetime.fromisoformat(iso_time).timestamp() if iso_time else None


def issue(inp, approval, execution=None, entered=None):
    """Idempotent per workflow execution: the workflow retries this task, and every retry must produce the
    same alert id, CAP file and decision record instead of a second alert. The id derives from the
    execution name and the content time from when the Issue state was entered (constant across retries)."""
    issued = epoch(entered) or time.time()
    zone = zones.get_item(Key={"zone_id": inp["zone_id"]})["Item"]
    zone["polygon"] = json.loads(zone["polygon"])
    tier, hazard = inp["tier"], inp.get("hazard", "flood")
    # The zone catalogue decides what is a test: the IoT bypass input carries no is_test flag, and a
    # heartbeat or e2e alert must never go out as "Actual".
    is_test = bool(inp.get("is_test")) or bool(zone.get("is_test"))
    status = "Test" if is_test else ("Exercise" if inp.get("drill") else "Actual")
    day = datetime.fromtimestamp(issued, UTC).strftime("%Y%m%d")
    suffix = hashlib.sha256(execution.encode()).hexdigest()[:6] if execution else uuid.uuid4().hex[:6]
    alert_id = f"SM-{day}-{inp['zone_id']}-{suffix.upper()}"
    mode = approval.get("decision") or ("auto" if tier != "warning" else "timeout")
    trigger_rx_ms = int(inp.get("trigger_rx_ms") or issued * 1000)
    references = []
    if tier == "clear":
        # An all-clear speaks for the alert it ends: same hazard, and an exercise's all-clear is an exercise.
        ended = previous_alerts(inp.get("references"))
        references = [cap.reference(a["alert_id"], int(a["issued_at"])) for a in ended]
        if ended:
            hazard = ended[-1].get("hazard", hazard)
            if not is_test and ended[-1].get("status") in ("Exercise", "Test"):
                status = ended[-1]["status"]

    xml = cap.build(alert_id, zone, tier, hazard, issued, status=status, references=references)
    cap_key = f"cap/{day}/{alert_id}.xml"
    s3.put_object(Bucket=os.environ["WEB_BUCKET"], Key=cap_key, Body=xml.encode("utf-8"),
                  ContentType="application/cap+xml", CacheControl="public, max-age=31536000, immutable")
    cap_sha = hashlib.sha256(xml.encode("utf-8")).hexdigest()

    texts = messages.render_all(tier, hazard, inp["zone_id"], alert_id[-6:])
    try:
        alerts.put_item(Item={
            "alert_id": alert_id, "sk": "META", "day": day, "issued_at": int(issued),
            "zone_id": inp["zone_id"], "zone_name": zone["name"], "district": zone["district"],
            "tier": tier, "hazard": hazard, "status": status, "mode": mode, "cell": os.environ["CELL"],
            "score": Decimal(str(inp.get("score", 0))), "reason": inp.get("reason", ""),
            "trigger_rx_ms": trigger_rx_ms, "cap_key": cap_key,
            "texts": texts, "origin": inp.get("origin", "scorer"), "sent": 0, "delivered": 0,
        }, ConditionExpression="attribute_not_exists(alert_id)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        log("alert_retry", alert_id=alert_id)  # a retry of this execution; the record already exists
    if tier != "clear":
        states.update_item(Key={"zone_id": inp["zone_id"]}, UpdateExpression="SET last_alert_id = :a",
                           ExpressionAttributeValues={":a": alert_id})
    if tier == "warning" and mode == "timeout":
        settle_approval(approval.get("approval_id") or inp.get("approval_id"), "AUTO_ISSUED", OPEN)
    record_decision(inp, mode=mode, approval=approval, alert_id=alert_id, cap_sha=cap_sha, execution=execution, at=issued)

    if not is_test:
        refresh_public_feed()
        tell_district_node(zone["district"], alert_id, inp["zone_id"], tier, status, issued)

    lag_ms = time.time() * 1000 - trigger_rx_ms
    metric("DecisionLatencyMs", lag_ms, "Milliseconds", Tier=tier, Mode=mode)
    log("alert_issued", alert_id=alert_id, zone_id=inp["zone_id"], tier=tier, mode=mode, status=status)

    message = {"type": "alert", "alert_id": alert_id, "zone_id": inp["zone_id"], "tier": tier,
               "hazard": hazard, "texts": texts, "status": status, "trigger_rx_ms": trigger_rx_ms}
    summary = (f"{status} {tier.upper()} {hazard} alert {alert_id} issued for {zone['name']} "
               f"({zone['district']}). Mode: {mode}. Score {inp.get('score')}. Reason: {inp.get('reason')}.")
    return {"alert_id": alert_id, "queue_url": QUEUES[tier], "message": json.dumps(message, ensure_ascii=False),
            "summary": summary, "is_test": is_test}


def tell_district_node(district, alert_id, zone_id, tier, status, issued):
    """Let the district's Local Alert Node know the cloud has issued this alert, so it doesn't issue its own
    (best effort: a node that hears nothing waits its grace period, then issues anyway)."""
    if iot is None:
        return
    try:
        iot.publish(topic=f"sm/alerts/{slug(district)}", qos=1, payload=json.dumps(
            {"alert_id": alert_id, "zone_id": zone_id, "tier": tier, "status": status, "issued_at": int(issued)}))
    except ClientError as exc:
        log("district_node_notify_failed", district=district, error=str(exc))


def refresh_public_feed():
    """Put the new alert in the public CAP feed now rather than at its next minute (best effort)."""
    if not os.environ.get("PUBLIC_FEED_FUNCTION"):
        return
    try:
        lam.invoke(FunctionName=os.environ["PUBLIC_FEED_FUNCTION"], InvocationType="Event", Payload=b"{}")
    except ClientError as exc:
        log("public_feed_refresh_failed", error=str(exc))


def previous_alerts(alert_ids):
    """The alert records an all-clear ends (those that can still be found)."""
    found = (alerts.get_item(Key={"alert_id": a, "sk": "META"}).get("Item") for a in alert_ids or [])
    return [item for item in found if item]


def settle_approval(approval_id, status, from_statuses):
    """Move an approval to its final status if it is still in one of from_statuses (race-safe with officers)."""
    if not approval_id:
        return
    names = {f":f{i}": s for i, s in enumerate(from_statuses)}
    try:
        approvals.update_item(
            Key={"approval_id": approval_id},
            UpdateExpression="SET #s = :new REMOVE pending",
            ConditionExpression=f"#s IN ({', '.join(names)})",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":new": status, **names},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def record_decision(inp, mode, approval=None, alert_id=None, cap_sha=None, execution=None, at=None):
    """Write-once decision record. The key is deterministic per alert (or per execution for a warning that was
    not issued) and `at` is the workflow state's entry time, so a retried task finds the record already there
    instead of writing a second one."""
    at = at or time.time()
    day = datetime.fromtimestamp(at, UTC).strftime("%Y/%m/%d")
    name = alert_id or execution or f"{int(at * 1000)}-{uuid.uuid4().hex[:6]}"
    key = f"decisions/{day}/{inp['zone_id']}/{name}-{mode}.json"
    body = {
        "recorded_at": datetime.fromtimestamp(at, UTC).isoformat(), "mode": mode, "alert_id": alert_id,
        "zone_id": inp["zone_id"], "tier": inp["tier"], "hazard": inp.get("hazard"), "score": inp.get("score"),
        "reason": inp.get("reason"), "contributions": inp.get("contributions"), "agreeing": inp.get("agreeing"),
        "evidence": inp.get("evidence"), "origin": inp.get("origin"), "cell": os.environ["CELL"],
        "approved_by": (approval or {}).get("decided_by"), "approval_note": (approval or {}).get("note"),
        "cap_sha256": cap_sha, "rules_version": os.environ.get("RULES_VERSION", "dev"),
    }
    raw = json.dumps(body, default=str, ensure_ascii=False).encode("utf-8")
    try:
        s3.put_object(Bucket=os.environ["DECISIONS_BUCKET"], Key=key, IfNoneMatch="*", Body=raw,
                      ContentType="application/json", ChecksumAlgorithm="SHA256")
    except ClientError as exc:
        # 412: a previous attempt already wrote (and chained) this record; 409: a concurrent attempt is writing it.
        if exc.response["Error"]["Code"] not in ("PreconditionFailed", "ConditionalRequestConflict"):
            raise
        log("decision_exists", key=key)
        return {"decision_key": key}
    out = {"decision_key": key}
    if meta is not None:
        out["chain"] = chain.append(s3, os.environ["DECISIONS_BUCKET"], meta, key, raw, at=at)
    return out
