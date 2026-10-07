"""Cell scorer: consumes the cell's observations stream (and a 1-minute watchdog tick), scores
each zone, stores its state and starts the cell's escalation workflow when the tier rises.

Environment: ZONES_TABLE, STATE_TABLE, CELL, STATE_MACHINE_ARN (optional until the workflow exists).
"""

import json
import os
import time
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

from common import scoring as sc
from common import zonestate
from common.observe import log, metric

ddb = boto3.resource("dynamodb")
sfn = boto3.client("stepfunctions")
zones_table = ddb.Table(os.environ["ZONES_TABLE"])
state_table = ddb.Table(os.environ["STATE_TABLE"])
CELL = os.environ["CELL"]
STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")
_deser = TypeDeserializer()


def handler(event, context):
    now = time.time()
    if event.get("mode") == "watchdog":
        return watchdog(now)

    by_zone = {}
    for record in event.get("Records", []):
        image = record.get("dynamodb", {}).get("NewImage")
        if not image:
            continue
        obs = {k: _deser.deserialize(v) for k, v in image.items()}
        by_zone.setdefault(obs["zone_id"], []).append(obs)

    failures = []
    for zone_id, observations in by_zone.items():
        try:
            score_zone(zone_id, observations, now)
            oldest = min(int(o.get("rx_ms", now * 1000)) for o in observations)
            metric("ScoreLatencyMs", now * 1000 - oldest, "Milliseconds", Cell=CELL)
        except Exception as exc:  # report the zone's records as failed so only they are retried
            log("score_failed", zone_id=zone_id, error=str(exc))
            failures += [
                {"itemIdentifier": r["dynamodb"]["SequenceNumber"]}
                for r in event["Records"]
                if r.get("dynamodb", {}).get("Keys", {}).get("zone_id", {}).get("S") == zone_id
            ]
    return {"batchItemFailures": failures}


def watchdog(now):
    """Re-score this cell's zones with no new data so decay, de-escalation and silence detection still run."""
    zones = zones_table.scan(FilterExpression="owner_cell = :c", ExpressionAttributeValues={":c": CELL})["Items"]
    blind = 0
    for zone in zones:
        state = score_zone(zone["zone_id"], [], now, zone=zone, only_if_known=True)
        blind += bool(state and state.get("blind"))
    metric("BlindZones", blind, Cell=CELL)
    return {"zones": len(zones), "blind": blind}


def score_zone(zone_id, observations, now, zone=None, only_if_known=False):
    zone = zone or zones_table.get_item(Key={"zone_id": zone_id})["Item"]
    zone = {k: (float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, bool) else v) for k, v in zone.items()}

    for _ in range(3):  # optimistic concurrency on "version"
        item = state_table.get_item(Key={"zone_id": zone_id}, ConsistentRead=True).get("Item")
        if item is None and only_if_known:
            return None
        version = int(item["version"]) if item else 0
        doc = json.loads(item["doc"]) if item else zonestate.empty_doc()
        previous_tier = item["tier"] if item else "normal"

        zonestate.merge(doc, observations, now)
        assessment, tier, went_up = zonestate.evaluate(zone, doc, previous_tier, now)
        drill = any(o.get("drill") for o in observations)

        # Start the workflow before saving state: if we crash in between, the retry rebuilds the
        # same execution name and Step Functions collapses the duplicate.
        if went_up and sc.RANK[tier] >= sc.RANK["advisory"]:
            start_escalation(zone, tier, assessment, doc, observations, drill, now)
        if tier != previous_tier:
            metric("TierChange", 1, Cell=CELL, Tier=tier)
            log("tier_change", zone_id=zone_id, cell=CELL, frm=previous_tier, to=tier, score=assessment.score,
                reason=assessment.reason, agreeing=assessment.agreeing)

        doc["contributions"], doc["agreeing"], doc["reason"] = assessment.contributions, assessment.agreeing, assessment.reason
        try:
            state_table.update_item(
                Key={"zone_id": zone_id},
                UpdateExpression="SET version = :nv, tier = :t, score = :s, blind = :b, hazard = :h, doc = :d, "
                "updated_at = :u, eval_cell = :c, district = :dist",
                ConditionExpression="attribute_not_exists(version) OR version = :v",
                ExpressionAttributeValues={
                    ":nv": version + 1, ":v": version, ":t": tier, ":s": Decimal(str(assessment.score)),
                    ":b": assessment.blind, ":h": assessment.hazard, ":d": json.dumps(doc), ":u": int(now),
                    ":c": CELL, ":dist": zone.get("district", ""),
                },
            )
            return {"tier": tier, "blind": assessment.blind}
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
    raise RuntimeError(f"state update conflict for {zone_id}")


def start_escalation(zone, tier, assessment, doc, observations, drill, now):
    if not STATE_MACHINE_ARN:
        return
    trigger_ms = min((int(o["rx_ms"]) for o in observations if o.get("rx_ms")), default=int(now * 1000))
    payload = {
        "zone_id": zone["zone_id"],
        "tier": tier,
        "hazard": assessment.hazard,
        "reason": assessment.reason,
        "score": assessment.score,
        "contributions": assessment.contributions,
        "agreeing": assessment.agreeing,
        "blind": assessment.blind,
        "evidence": doc["ev"],
        "episode": doc["episode"],
        "drill": drill,
        "is_test": bool(zone.get("is_test")),
        "approval_timeout_s": int(zone.get("approval_timeout_s", 300)),
        "trigger_rx_ms": trigger_ms,
        "origin": "scorer",
        "cell": CELL,
    }
    name = sc.execution_name(zone["zone_id"], tier, doc["episode"], drill)
    try:
        sfn.start_execution(stateMachineArn=STATE_MACHINE_ARN, name=name, input=json.dumps(payload))
        log("escalation_started", zone_id=zone["zone_id"], tier=tier, execution=name)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ExecutionAlreadyExists":
            raise
