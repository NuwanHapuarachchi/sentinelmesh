"""Heartbeat: every 10 minutes, push one synthetic reading through the WHOLE alert path in the
hidden test zone H00 (IoT -> cell -> scorer/bypass -> workflow -> issuer -> queue -> dispatch -> phone inbox)
and time it. Monitoring measures reach, not uptime: if this stops arriving, an alarm fires.

Environment: ZONES_TABLE, STATE_TABLE, INBOX_TABLE, IOT_ENDPOINT.
"""

import json
import os
import time

import boto3
from boto3.dynamodb.conditions import Key

from common.observe import log, metric

ZONE = "H00"
SUBSCRIBER = "heartbeat-H00"
ddb = boto3.resource("dynamodb")
iot = boto3.client("iot-data", endpoint_url=f"https://{os.environ['IOT_ENDPOINT']}")
zones = ddb.Table(os.environ["ZONES_TABLE"])
states = ddb.Table(os.environ["STATE_TABLE"])
inbox = ddb.Table(os.environ["INBOX_TABLE"])
subscribers = ddb.Table(os.environ["SUBSCRIBERS_TABLE"])


def handler(event, context):
    zone = zones.get_item(Key={"zone_id": ZONE})["Item"]
    cell = zone["owner_cell"]
    subscribers.put_item(Item={"subscriber_id": SUBSCRIBER, "zone_id": ZONE, "lang": "en", "geohash6": "heartb", "consent": "system"})
    states.delete_item(Key={"zone_id": ZONE})  # reset the zone and its alert gate so every beat alerts
    start_ms = int(time.time() * 1000)
    iot.publish(topic=f"sm/obs/{ZONE}", qos=1, payload=json.dumps({
        "source_class": "gauge", "source_id": "heartbeat", "ts": time.time(), "hb_id": str(start_ms),
        "level_m": float(zone["danger_level_m"]) + 0.5,
    }))

    deadline = time.time() + 90
    while time.time() < deadline:
        items = inbox.query(KeyConditionExpression=Key("subscriber_id").eq(SUBSCRIBER) & Key("sk").gt(str(start_ms)))["Items"]
        if items:
            e2e_ms = int(items[0]["sk"].split("#")[0]) - start_ms
            metric("HeartbeatE2EMs", e2e_ms, "Milliseconds", Cell=cell)
            metric("HeartbeatOK", 1, Cell=cell)
            log("heartbeat_ok", cell=cell, e2e_ms=e2e_ms)
            return {"ok": True, "e2e_ms": e2e_ms}
        time.sleep(2)
    metric("HeartbeatOK", 0, Cell=cell)
    log("heartbeat_missed", cell=cell)
    return {"ok": False}
