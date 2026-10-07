"""Scripted drills for the demo. Every observation is published to the real IoT topic with
drill=true, so it flows through the same routing, scoring and workflow as live data, and alerts
come out as CAP status "Exercise".

flood_corroboration  gauge rises alone (watch), then forecast and resident reports agree (warning),
                     then heavy rain arrives (imminent)
single_gauge         one gauge spikes and nothing else agrees: stays at watch
sensor_silence       gauge rises, then the station goes silent: zone turns blind and escalates
dam_surge_bypass     a gauge jumps straight past the danger level: hard-threshold path

Environment: ZONES_TABLE, IOT_ENDPOINT.
"""

import json
import os
import time

import boto3

from common.observe import log

ddb = boto3.resource("dynamodb")
iot = boto3.client("iot-data", endpoint_url=f"https://{os.environ['IOT_ENDPOINT']}")
zones = ddb.Table(os.environ["ZONES_TABLE"])


def handler(event, context):
    zone = zones.get_item(Key={"zone_id": event["zone_id"]})["Item"]
    warning, danger = float(zone["warning_level_m"]), float(zone["danger_level_m"])
    zid = zone["zone_id"]

    def level(severity):
        return round(warning + (severity - 0.6) / 0.4 * (danger - warning), 2)

    def gauge(sev):
        return ("gauge", {"level_m": level(sev)})

    reports = [("report", {"reporter_hash": f"drill-{i}", "report_kind": "flooding", "report_sev": 3}) for i in range(2)]
    steps = {
        "single_gauge": [(0, [gauge(0.70)]), (15, [gauge(0.80)]), (15, [gauge(0.82)])],
        "flood_corroboration": [
            (0, [gauge(0.70)]), (15, [gauge(0.80)]),
            (20, [("forecast", {"fc_rain_24h_mm": 95}), *reports]),
            (45, [gauge(0.86), ("rain", {"rain_24h_mm": 135})]),
        ],
        "sensor_silence": [(0, [gauge(0.62)]), (15, [gauge(0.72)]), (15, [gauge(0.82)])],
        "dam_surge_bypass": [(0, [gauge(0.65)]), (10, [("gauge", {"level_m": round(danger + 0.4, 2)})])],
    }[event["scenario"]]

    for delay, observations in steps:
        time.sleep(delay)
        for cls, values in observations:
            iot.publish(topic=f"sm/obs/{zid}", qos=1, payload=json.dumps({
                "source_class": cls, "source_id": f"drill-{cls}", "ts": time.time(), "drill": True,
                "scenario_id": event["scenario"], **values,
            }))
        log("scenario_step", scenario=event["scenario"], zone_id=zid, observations=[c for c, _ in observations])
    return {"done": event["scenario"], "zone_id": zid}
