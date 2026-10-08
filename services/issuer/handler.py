"""Issuer: called by the escalation workflow.

op=issue  -> alert id, CAP 1.2 file, alert record, decision record, message for the tier queue.
op=record -> decision record only, for a warning that was not issued:
             mode=rejected   (an officer held it back)
             mode=superseded (a higher tier was issued, or the zone fell below advisory, while it waited)

Decision records are write-once: the bucket policy rejects any PutObject without If-None-Match.
Environment: ALERTS_TABLE, APPROVALS_TABLE, ZONES_TABLE, WEB_BUCKET, DECISIONS_BUCKET, CELL,
QUEUE_IMMINENT, QUEUE_WARNING, QUEUE_ADVISORY, RULES_VERSION.
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

from common import cap, messages
from common.observe import log, metric

ddb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
alerts = ddb.Table(os.environ["ALERTS_TABLE"])
approvals = ddb.Table(os.environ["APPROVALS_TABLE"])
zones = ddb.Table(os.environ["ZONES_TABLE"])
QUEUES = {t: os.environ[f"QUEUE_{t.upper()}"] for t in ("imminent", "warning", "advisory")}
# Approval states the workflow may still move an approval out of (an officer may be mid-decision).
OPEN = ("PENDING", "DECIDING")


def handler(event, context):
    if event.get("op") == "record":
        mode = event.get("mode", "rejected")
        if mode not in ("rejected", "superseded"):
            raise ValueError(f"unknown record mode {mode!r}")
        approval = event.get("approval") or {}
        if mode == "superseded":
            # An approved warning can still be superseded before it issues, so APPROVED moves too.
            settle_approval(approval.get("approval_id"), "SUPERSEDED", (*OPEN, "APPROVED"))
        return record_decision(event["input"], mode=mode, approval=approval)
    return issue(event["input"], event.get("approval") or {})


def issue(inp, approval):
    now = time.time()
    zone = zones.get_item(Key={"zone_id": inp["zone_id"]})["Item"]
    zone["polygon"] = json.loads(zone["polygon"])
    tier, hazard = inp["tier"], inp.get("hazard", "flood")
    # The zone catalogue decides what is a test: the IoT bypass input carries no is_test flag, and a
    # heartbeat or e2e alert must never go out as "Actual".
    is_test = bool(inp.get("is_test")) or bool(zone.get("is_test"))
    status = "Test" if is_test else ("Exercise" if inp.get("drill") else "Actual")
    day = datetime.fromtimestamp(now, UTC).strftime("%Y%m%d")
    alert_id = f"SM-{day}-{inp['zone_id']}-{uuid.uuid4().hex[:6].upper()}"
    mode = approval.get("decision") or ("auto" if tier != "warning" else "timeout")

    xml = cap.build(alert_id, zone, tier, hazard, now, status=status)
    cap_key = f"cap/{day}/{alert_id}.xml"
    s3.put_object(Bucket=os.environ["WEB_BUCKET"], Key=cap_key, Body=xml.encode("utf-8"),
                  ContentType="application/cap+xml", CacheControl="public, max-age=31536000, immutable")
    cap_sha = hashlib.sha256(xml.encode("utf-8")).hexdigest()

    texts = messages.render_all(tier, hazard, inp["zone_id"], alert_id[-6:])
    alerts.put_item(Item={
        "alert_id": alert_id, "sk": "META", "day": day, "issued_at": int(now),
        "zone_id": inp["zone_id"], "zone_name": zone["name"], "district": zone["district"],
        "tier": tier, "hazard": hazard, "status": status, "mode": mode, "cell": os.environ["CELL"],
        "score": Decimal(str(inp.get("score", 0))), "reason": inp.get("reason", ""),
        "trigger_rx_ms": int(inp.get("trigger_rx_ms") or now * 1000), "cap_key": cap_key,
        "texts": texts, "origin": inp.get("origin", "scorer"), "sent": 0, "delivered": 0,
    })
    if tier == "warning" and mode == "timeout":
        settle_approval(approval.get("approval_id") or inp.get("approval_id"), "AUTO_ISSUED", OPEN)
    record_decision(inp, mode=mode, approval=approval, alert_id=alert_id, cap_sha=cap_sha)

    lag_ms = now * 1000 - int(inp.get("trigger_rx_ms") or now * 1000)
    metric("DecisionLatencyMs", lag_ms, "Milliseconds", Tier=tier, Mode=mode)
    log("alert_issued", alert_id=alert_id, zone_id=inp["zone_id"], tier=tier, mode=mode, status=status)

    message = {"type": "alert", "alert_id": alert_id, "zone_id": inp["zone_id"], "tier": tier,
               "hazard": hazard, "texts": texts, "status": status,
               "trigger_rx_ms": int(inp.get("trigger_rx_ms") or now * 1000)}
    summary = (f"{status} {tier.upper()} {hazard} alert {alert_id} issued for {zone['name']} "
               f"({zone['district']}). Mode: {mode}. Score {inp.get('score')}. Reason: {inp.get('reason')}.")
    return {"alert_id": alert_id, "queue_url": QUEUES[tier], "message": json.dumps(message, ensure_ascii=False),
            "summary": summary, "is_test": is_test}


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


def record_decision(inp, mode, approval=None, alert_id=None, cap_sha=None):
    now = time.time()
    day = datetime.fromtimestamp(now, UTC).strftime("%Y/%m/%d")
    key = f"decisions/{day}/{inp['zone_id']}/{int(now * 1000)}-{mode}-{uuid.uuid4().hex[:6]}.json"
    body = {
        "recorded_at": datetime.fromtimestamp(now, UTC).isoformat(), "mode": mode, "alert_id": alert_id,
        "zone_id": inp["zone_id"], "tier": inp["tier"], "hazard": inp.get("hazard"), "score": inp.get("score"),
        "reason": inp.get("reason"), "contributions": inp.get("contributions"), "agreeing": inp.get("agreeing"),
        "evidence": inp.get("evidence"), "origin": inp.get("origin"), "cell": os.environ["CELL"],
        "approved_by": (approval or {}).get("decided_by"), "approval_note": (approval or {}).get("note"),
        "cap_sha256": cap_sha, "rules_version": os.environ.get("RULES_VERSION", "dev"),
    }
    s3.put_object(Bucket=os.environ["DECISIONS_BUCKET"], Key=key, IfNoneMatch="*",
                  Body=json.dumps(body, default=str, ensure_ascii=False).encode("utf-8"),
                  ContentType="application/json", ChecksumAlgorithm="SHA256")
    return {"decision_key": key}
