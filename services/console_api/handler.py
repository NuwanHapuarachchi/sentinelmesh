"""Agency console API (Cognito). Each agency sees what its job needs, and the filtering happens here, at
the API, not in the browser:

  role          zones                                   alerts                          actions
  duty_officer  all, with evidence and scores           all, with delivery counts       drills, failover
  met           all, with evidence and scores (sensors) all, without people counts      -
  ems           own district (custom:district), no      own district, with counts       -
                evidence
  ngo, viewer   counts per district and tier            counts per district and tier    -

A user in several groups gets the most capable role. Approvals are a separate function (duty_officer).

GET  /zones                     zone states for the caller's role
GET  /alerts                    alerts issued today and yesterday, for the caller's role
POST /scenarios/{id}            start a scripted drill {"zone_id": "Z07"} (duty_officer)
POST /cells/{cell}/failover     cell {cell} is lost: the warm standby adopts its zones (a data change) (duty_officer)
POST /cells/{cell}/restore      return {cell}'s zones to it (duty_officer)

Environment: ZONES_TABLE, STATE_TABLE, ALERTS_TABLE, SCENARIO_FUNCTION, PREFIX, ACTIVE_CELLS ("a,b"), STANDBY_CELL ("c").
"""

import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common import briefing
from common.api import claims_of, groups_of, respond
from common.observe import log

ddb = boto3.resource("dynamodb")
lam = boto3.client("lambda")
zones = ddb.Table(os.environ["ZONES_TABLE"])
states = ddb.Table(os.environ["STATE_TABLE"])
alerts = ddb.Table(os.environ["ALERTS_TABLE"])
SCENARIOS = {"flood_corroboration", "single_gauge", "sensor_silence", "dam_surge_bypass"}
PREFIX = os.environ.get("PREFIX", "sentinelmesh")
ACTIVE_CELLS = [c for c in os.environ.get("ACTIVE_CELLS", "a,b").split(",") if c]
STANDBY_CELL = os.environ.get("STANDBY_CELL", "c")

ROLES = ("duty_officer", "met", "ems", "ngo", "viewer")  # most capable first
ZONE_BASE = ("zone_id", "name", "district", "owner_cell", "tier", "blind")
ZONE_FIELDS = {
    "duty_officer": ZONE_BASE + ("score", "hazard", "eval_cell", "reason", "agreeing", "contributions", "evidence", "updated_at"),
    "met": ZONE_BASE + ("score", "hazard", "eval_cell", "reason", "agreeing", "contributions", "evidence", "updated_at"),
    "ems": ZONE_BASE + ("hazard", "updated_at"),
}
ALERT_BASE = ("alert_id", "zone_id", "zone_name", "district", "tier", "hazard", "status", "issued_at", "cap_key", "texts")
ALERT_FIELDS = {
    "duty_officer": ALERT_BASE + ("mode", "sent", "delivered", "first_receipt_ms", "origin", "cell"),
    "met": ALERT_BASE + ("mode", "origin", "cell"),
    "ems": ALERT_BASE + ("sent", "delivered", "first_receipt_ms"),
}
HIDDEN_PREFIXES = ("T", "H", "L")


def role_of(event):
    groups = groups_of(event)
    return next((r for r in ROLES if r in groups), None)


def handler(event, context):
    role = role_of(event)
    if role is None:
        return respond(403, {"error": "no SentinelMesh group"})
    district = claims_of(event).get("custom:district") or None
    if role == "ems" and not district:
        return respond(403, {"error": "ems users need a district (custom:district)"})
    method, resource = event.get("httpMethod"), event.get("resource")

    if (method, resource) == ("GET", "/zones"):
        return list_zones(role, district)
    if (method, resource) == ("GET", "/alerts"):
        return list_alerts(role, district)
    if (method, resource) == ("POST", "/zones/{id}/briefing"):
        if role not in ("duty_officer", "met"):
            return respond(403, {"error": "sensor evidence access required"})
        return zone_briefing((event.get("pathParameters") or {}).get("id"))
    if role != "duty_officer":
        return respond(403, {"error": "duty_officer group required"})
    params = event.get("pathParameters") or {}
    body = json.loads(event.get("body") or "{}")
    if resource == "/scenarios/{id}":
        return start_scenario(params.get("id"), body)
    if resource == "/cells/{cell}/failover":
        return move_cell(params.get("cell"), failover=True)
    if resource == "/cells/{cell}/restore":
        return move_cell(params.get("cell"), failover=False)
    return respond(404, {"error": "not found"})


def zone_briefing(zone_id):
    if os.environ.get("GEMINI_ENABLED") != "true":
        return respond(503, {"error": "AI briefing is disabled"})
    if not isinstance(zone_id, str) or len(zone_id) != 3 or not zone_id.startswith("Z") or not zone_id[1:].isdigit():
        return respond(400, {"error": "invalid public zone"})
    try:
        zone = zones.get_item(Key={"zone_id": zone_id}).get("Item")
        if not zone or zone.get("is_test"):
            return respond(404, {"error": "zone not found"})
        # Existing scan permission; no new read access to observations or resident data.
        state = next(iter(scan_all(states, FilterExpression="zone_id = :z",
                                  ExpressionAttributeValues={":z": zone_id})), None)
        if not state or int(time.time()) - int(state.get("updated_at", 0)) > 300:
            return respond(409, {"error": "Recent sensor evidence is unavailable; refresh before analysis"})
        data = briefing.snapshot(zone, state, time.time())
        model = os.environ.get("GEMINI_MODEL", "")
        limits = ddb.Table(os.environ["RATELIMITS_TABLE"])
        # Remove changing age/capture fields from fingerprint; snapshot timestamp stays visible.
        fingerprint = {k: {a: b for a, b in v.items() if a != "age_seconds"} for k, v in data["facts"].items()}
        digest = hashlib.sha256(json.dumps([zone_id, briefing.VERSION, model, fingerprint], sort_keys=True).encode()).hexdigest()
        key = {"k": "ai-cache#" + digest}
        cached = limits.get_item(Key=key).get("Item")
        now = int(time.time())
        if cached and int(cached["expires_at"]) > now:
            return respond(200, json.loads(cached["result"]))
        # Account-wide budgets work across concurrent Lambda instances; no paid retry loop.
        for window, seconds, cap in (("minute", 60, 5), ("day", 86400, 30)):
            try:
                limits.update_item(Key={"k": f"ai-budget#{window}#{now // seconds}"},
                    UpdateExpression="SET expires_at = :expiry ADD n :one",
                    ConditionExpression="attribute_not_exists(n) OR n < :cap",
                    ExpressionAttributeValues={":expiry": now + seconds * 2, ":one": 1, ":cap": cap})
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    return respond(429, {"error": "AI briefing request limit reached; try later"})
                raise
        secret = json.loads(boto3.client("secretsmanager").get_secret_value(
            SecretId=os.environ["GEMINI_SECRET_ARN"])["SecretString"])
        api_key = secret.get("gemini_api_key")
        if not api_key or not model:
            return respond(503, {"error": "AI briefing is not configured"})
        result = {**briefing.generate(data, api_key, model), "zone_id": zone_id,
                  "captured_at": data["captured_at"], "evidence": data["facts"],
                  "history_sufficient": data["history_sufficient"], "model": model,
                  "notice": "AI-assisted evidence review. Not a validated forecast or warning decision."}
        limits.put_item(Item={**key, "expires_at": now + 120, "result": json.dumps(result)})
        return respond(200, result)
    except Exception:
        # Never log API keys, model prompts, personal data or provider error bodies.
        return respond(503, {"error": "AI briefing unavailable; existing warning operations remain available"})


def scan_all(table, **kwargs):
    items, page = [], table.scan(**kwargs)
    items += page["Items"]
    while "LastEvaluatedKey" in page:
        page = table.scan(ExclusiveStartKey=page["LastEvaluatedKey"], **kwargs)
        items += page["Items"]
    return items


def counts(rows):
    out = {}
    for r in rows:
        district, tier = r.get("district") or "unknown", r.get("tier", "normal")
        out.setdefault(district, {}).setdefault(tier, 0)
        out[district][tier] += 1
    return out


def list_zones(role, district=None):
    cfg = {z["zone_id"]: z for z in scan_all(zones) if not z.get("is_test")}
    st = {s["zone_id"]: s for s in scan_all(states)}
    rows = []
    for zid, z in sorted(cfg.items()):
        s = st.get(zid, {})
        doc = json.loads(s["doc"]) if s.get("doc") else {}
        rows.append({
            "zone_id": zid, "name": z["name"], "district": z["district"], "owner_cell": z["owner_cell"],
            "tier": s.get("tier", "normal"), "blind": bool(s.get("blind")), "score": s.get("score", 0),
            "hazard": s.get("hazard", z.get("hazard")), "eval_cell": s.get("eval_cell"), "reason": doc.get("reason"),
            "agreeing": doc.get("agreeing"), "contributions": doc.get("contributions"), "evidence": doc.get("ev"),
            "updated_at": s.get("updated_at"),
        })
    if role not in ZONE_FIELDS:
        return respond(200, {"districts": counts(rows)})
    if role == "ems":
        rows = [r for r in rows if r["district"] == district]
    return respond(200, {"zones": [{k: r[k] for k in ZONE_FIELDS[role]} for r in rows]})


def list_alerts(role, district=None):
    items = []
    for days_ago in (0, 1):
        day = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y%m%d")
        items += alerts.query(IndexName="by-day", KeyConditionExpression=Key("day").eq(day), ScanIndexForward=False)["Items"]
    # Hidden test, heartbeat and load zones (T, H, L) never appear, whatever status their alerts carry.
    items = [i for i in items if i.get("status") != "Test" and not str(i.get("zone_id", "")).startswith(HIDDEN_PREFIXES)]
    if role not in ALERT_FIELDS:
        return respond(200, {"count": len(items), "districts": counts(items)})
    if role == "ems":
        items = [i for i in items if i.get("district") == district]
    return respond(200, {"alerts": [{k: i.get(k) for k in ALERT_FIELDS[role]} for i in items][:50]})


def start_scenario(scenario, body):
    zone_id = body.get("zone_id", "Z07")
    if scenario not in SCENARIOS:
        return respond(400, {"error": f"scenario must be one of {sorted(SCENARIOS)}"})
    if not zones.get_item(Key={"zone_id": zone_id}).get("Item"):
        return respond(400, {"error": "unknown zone"})
    lam.invoke(FunctionName=os.environ["SCENARIO_FUNCTION"], InvocationType="Event",
               Payload=json.dumps({"scenario": scenario, "zone_id": zone_id}))
    log("scenario_started", scenario=scenario, zone_id=zone_id)
    return respond(202, {"started": scenario, "zone_id": zone_id})


def move_cell(cell, failover):
    """Failover is a data change: a lost active cell's zones get the standby as owner (the IoT rules route
    the next reading there); restore hands them back. Zones keep home_cell so restore knows where they live."""
    if cell not in ACTIVE_CELLS:
        return respond(400, {"error": f"cell must be one of {', '.join(ACTIVE_CELLS)}"})
    target = STANDBY_CELL if failover else cell
    moved = 0
    for z in scan_all(zones):
        home = z.get("home_cell", z["owner_cell"])
        if failover and z["owner_cell"] == cell:
            zones.update_item(Key={"zone_id": z["zone_id"]}, UpdateExpression="SET owner_cell = :o, home_cell = :h",
                              ExpressionAttributeValues={":o": target, ":h": home})
            moved += 1
        elif not failover and home == cell and z["owner_cell"] != cell:
            zones.update_item(Key={"zone_id": z["zone_id"]}, UpdateExpression="SET owner_cell = :o REMOVE home_cell",
                              ExpressionAttributeValues={":o": cell})
            moved += 1
    if moved:
        # Re-score the moved zones in their new cell now rather than at its next 1-minute watchdog tick.
        lam.invoke(FunctionName=f"{PREFIX}-{target}-scorer", InvocationType="Event", Payload=json.dumps({"mode": "watchdog"}))
    log("cell_moved", cell=cell, failover=failover, to=target, zones=moved, at=int(time.time()))
    return respond(200, {"cell": cell, "action": "failover" if failover else "restore", "to": target, "zones_moved": moved})
