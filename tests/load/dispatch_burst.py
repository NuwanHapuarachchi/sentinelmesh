"""Fan-out load test: how long the planner and senders take to reach every subscriber in one zone.

Seeds N simulated subscribers into hidden load zone L01 (cell a), raises L01 past its danger level with a
reading (the IoT bypass and the scorer issue an imminent alert, labelled Test because L01 is a test zone),
waits until the alert has been sent to its whole audience, and reports the planning and drain times. It fails
if the drain takes longer than MAX_DRAIN_S (default 300 s). It then removes the subscribers and the zone state
(inbox and ledger rows expire on their own TTLs).

    python tests/load/dispatch_burst.py [subscribers]        # default 10000
"""

import json
import os
import sys
import time

import boto3
from boto3.dynamodb.conditions import Attr

REGION = "ap-southeast-2"
ZONE = "L01"
COUNT = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000
MAX_DRAIN_S = float(os.environ.get("MAX_DRAIN_S", "300"))
LANGS = ("en", "si", "ta")


def subscriber(i):
    return {"subscriber_id": f"load-{i:06d}", "zone_id": ZONE, "lang": LANGS[i % 3], "geohash6": "loadts", "consent": "load-test"}


def seed(table, count):
    with table.batch_writer() as batch:
        for i in range(count):
            batch.put_item(Item=subscriber(i))


def clean(table, count):
    with table.batch_writer() as batch:
        for i in range(count):
            batch.delete_item(Key={"subscriber_id": f"load-{i:06d}"})


def summarise(meta, start, done_at):
    """Timings from the alert record: planning (reading to planned) and drain (planned to last send)."""
    planned = int(meta.get("planned_at", start))
    return {"audience": int(meta.get("audience", 0)), "sent": int(meta.get("sent", 0)), "batches": int(meta.get("batches", 0)),
            "reading_to_planned_s": round(planned - start, 1), "drain_s": round(done_at - planned, 1),
            "reading_to_last_send_s": round(done_at - start, 1)}


def main():
    s = boto3.session.Session(region_name=REGION)
    ddb = s.resource("dynamodb")
    subs, alerts, states, zones = (ddb.Table(f"sentinelmesh-{t}") for t in ("subscribers", "alerts", "zone-state", "zones"))
    endpoint = s.client("iot").describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    iot = s.client("iot-data", endpoint_url=f"https://{endpoint}")
    print(f"seeding {COUNT} subscribers into {ZONE}", flush=True)
    seed(subs, COUNT)
    ok = False
    try:
        states.delete_item(Key={"zone_id": ZONE})
        danger = float(zones.get_item(Key={"zone_id": ZONE})["Item"]["danger_level_m"])
        start = time.time()
        iot.publish(topic=f"sm/obs/{ZONE}", qos=1, payload=json.dumps(
            {"source_class": "gauge", "source_id": "dispatch-burst", "ts": start, "level_m": danger + 0.5}))
        meta, deadline = None, start + MAX_DRAIN_S + 120
        ours = Attr("zone_id").eq(ZONE) & Attr("sk").eq("META") & Attr("issued_at").gte(int(start) - 1)
        while time.time() < deadline:
            found = alerts.scan(FilterExpression=ours)["Items"]
            if found:
                meta = alerts.get_item(Key={"alert_id": found[0]["alert_id"], "sk": "META"}, ConsistentRead=True)["Item"]
                if meta.get("audience") is not None and int(meta.get("sent", 0)) >= int(meta["audience"]):
                    break
                print(f"  sent {meta.get('sent', 0)} / {meta.get('audience', '?')}", flush=True)
            time.sleep(5)
        if not meta:
            print("FAIL: no alert was issued")
            return 1
        report = summarise(meta, start, time.time())
        print(json.dumps(report, indent=1))
        ok = report["audience"] == COUNT and report["sent"] >= COUNT and report["drain_s"] <= MAX_DRAIN_S
        print(f"{'PASS' if ok else 'FAIL'}: {report['sent']} of {COUNT} sent, drain {report['drain_s']} s (limit {MAX_DRAIN_S:.0f} s)")
    finally:
        clean(subs, COUNT)
        states.delete_item(Key={"zone_id": ZONE})
        print(f"removed {COUNT} load subscribers and {ZONE}'s state")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
