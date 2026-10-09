"""Heartbeat: every 10 minutes, push one synthetic reading through the WHOLE alert path in each cell's
hidden heartbeat zone (IoT -> cell -> scorer/bypass -> workflow -> issuer -> queue -> dispatch -> phone inbox)
and time it. One zone per cell, including the warm standby, so every cell is proven, not assumed.
Monitoring measures reach, not uptime: if a zone's beat stops arriving, its alarm fires.

Metrics are per zone (the alarm's dimension, stable across failovers); the log line names the cell that
served the beat.
Environment: ZONES_TABLE, STATE_TABLE, INBOX_TABLE, SUBSCRIBERS_TABLE, IOT_ENDPOINT, HEARTBEAT_ZONES (e.g. "H00,H01,H02").
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import boto3
from boto3.dynamodb.conditions import Key

from common.observe import log, metric

ZONES = [z for z in os.environ.get("HEARTBEAT_ZONES", "H00").split(",") if z]
DEADLINE_S = 90
ddb = boto3.resource("dynamodb")
iot = boto3.client("iot-data", endpoint_url=f"https://{os.environ['IOT_ENDPOINT']}")
zones = ddb.Table(os.environ["ZONES_TABLE"])
states = ddb.Table(os.environ["STATE_TABLE"])
inbox = ddb.Table(os.environ["INBOX_TABLE"])
subscribers = ddb.Table(os.environ["SUBSCRIBERS_TABLE"])


def handler(event, context):
    with ThreadPoolExecutor(max_workers=len(ZONES)) as pool:
        results = list(pool.map(beat, ZONES))
    return {"beats": results, "ok": all(r["ok"] for r in results)}


def beat(zone_id):
    subscriber = f"heartbeat-{zone_id}"
    try:
        zone = zones.get_item(Key={"zone_id": zone_id})["Item"]
        cell = zone["owner_cell"]
        subscribers.put_item(Item={
            "subscriber_id": subscriber, "zone_id": zone_id, "lang": "en", "geohash6": "heartb", "consent": "system",
        })
        states.delete_item(Key={"zone_id": zone_id})  # reset the zone and its alert gate so every beat alerts
        start_ms = int(time.time() * 1000)
        iot.publish(topic=f"sm/obs/{zone_id}", qos=1, payload=json.dumps({
            "source_class": "gauge", "source_id": "heartbeat", "ts": time.time(), "hb_id": str(start_ms),
            "level_m": float(zone["danger_level_m"]) + 0.5,
        }))
    except Exception as exc:  # a beat that cannot even start is a failed beat, not a crashed heartbeat
        metric("HeartbeatOK", 0, Zone=zone_id)
        log("heartbeat_error", zone_id=zone_id, error=str(exc))
        return {"zone_id": zone_id, "ok": False}

    deadline = time.time() + DEADLINE_S
    while time.time() < deadline:
        items = inbox.query(KeyConditionExpression=Key("subscriber_id").eq(subscriber) & Key("sk").gt(str(start_ms)))["Items"]
        if items:
            e2e_ms = int(items[0]["sk"].split("#")[0]) - start_ms
            metric("HeartbeatE2EMs", e2e_ms, "Milliseconds", Zone=zone_id)
            metric("HeartbeatOK", 1, Zone=zone_id)
            log("heartbeat_ok", zone_id=zone_id, cell=cell, e2e_ms=e2e_ms)
            return {"zone_id": zone_id, "cell": cell, "ok": True, "e2e_ms": e2e_ms}
        time.sleep(2)
    metric("HeartbeatOK", 0, Zone=zone_id)
    log("heartbeat_missed", zone_id=zone_id, cell=cell)
    return {"zone_id": zone_id, "cell": cell, "ok": False}
