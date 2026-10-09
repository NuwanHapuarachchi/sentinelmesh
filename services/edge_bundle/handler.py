"""Edge bundle: every 5 minutes, pre-stage everything a district's Local Alert Node needs to warn its district
with no cloud at all (static stability): the district's zones (polygons and thresholds) and who to warn
(subscribers per zone, with their language and, for real SMS, their number).

One private object per node: s3://<evidence bucket>/edge/<node thing>/bundle.json, readable only by that node
(its certificate exchanges for a role scoped to its own thing name). The node keeps the last good bundle on
disk, so it can still issue from it when the cloud is gone.

Environment: ZONES_TABLE, SUBSCRIBERS_TABLE, EVIDENCE_BUCKET, NODE_PREFIX ("sentinelmesh-node-").
"""

import hashlib
import json
import os
import time

import boto3
from boto3.dynamodb.conditions import Key

from common.districts import slug

ddb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
zones = ddb.Table(os.environ["ZONES_TABLE"])
subscribers = ddb.Table(os.environ["SUBSCRIBERS_TABLE"])
NODE_PREFIX = os.environ.get("NODE_PREFIX", "sentinelmesh-node-")


def handler(event, context):
    now = int(time.time())
    by_district = {}
    for z in scan_all(zones):
        if not z.get("is_test"):
            by_district.setdefault(z["district"], []).append(z)
    written = {}
    for district, members in sorted(by_district.items()):
        bundle = build(district, members, now)
        key = f"edge/{NODE_PREFIX}{slug(district)}/bundle.json"
        body = json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode("utf-8")
        s3.put_object(Bucket=os.environ["EVIDENCE_BUCKET"], Key=key, Body=body, ContentType="application/json")
        written[district] = {"key": key, "zones": len(bundle["zones"]), "recipients": len(bundle["recipients"])}
    return {"bundles": written}


def build(district, members, now):
    zone_rows = [{
        "zone_id": z["zone_id"], "name": z["name"], "district": district, "hazard": z["hazard"],
        "polygon": json.loads(z["polygon"]), "warning_level_m": float(z["warning_level_m"]),
        "danger_level_m": float(z["danger_level_m"]), "gauge_cadence_s": int(z["gauge_cadence_s"]),
    } for z in sorted(members, key=lambda z: z["zone_id"])]
    recipients = []
    for z in zone_rows:
        for s in query_all(z["zone_id"]):
            recipients.append({"subscriber_id": s["subscriber_id"], "zone_id": z["zone_id"], "lang": s.get("lang", "en"),
                               **({"msisdn": s["msisdn"]} if s.get("msisdn") else {})})
    content = {"district": district, "slug": slug(district), "zones": zone_rows, "recipients": recipients}
    version = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:16]
    return {**content, "version": version, "generated_at": now}


def scan_all(table):
    items, page = [], table.scan()
    items += page["Items"]
    while "LastEvaluatedKey" in page:
        page = table.scan(ExclusiveStartKey=page["LastEvaluatedKey"])
        items += page["Items"]
    return items


def query_all(zone_id):
    items, kwargs = [], {"IndexName": "by-zone", "KeyConditionExpression": Key("zone_id").eq(zone_id)}
    while True:
        page = subscribers.query(**kwargs)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
