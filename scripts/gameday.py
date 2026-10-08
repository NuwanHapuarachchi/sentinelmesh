"""GameDay: lose a cell for real, prove the gap, fail over to the warm standby, and time the recovery.

  1. Baseline: nothing is happening in the zone.
  2. "Lose" the cell: disable its stream consumers (scorer, archiver) and IoT rules (routing and bypass).
  3. Publish a reading past the danger level. Nothing may be alerted: the cell is down (the gap).
  4. Fail over: the zone(s) get the standby as owner (a data change, as the console does).
  5. Publish again and time how long until the standby's alert is issued.
  6. Always restore: re-enable the cell, hand the zones back, clear the zone state.

    python scripts/gameday.py                       # test zone T01 only (default; alerts are labelled Test)
    python scripts/gameday.py --scope cell          # the whole cell's zones move to the standby, as in a real loss

Runs with the deploy role (manual CI job "gameday"). Prints a timing table; exits 1 if the standby did not
alert in time or the cell alerted while it was supposed to be down.
"""

import argparse
import json
import sys
import time

import boto3
from boto3.dynamodb.conditions import Attr

PREFIX = "sentinelmesh"
REGION = "ap-southeast-2"
RULES = ("route", "bypass", "route_devices", "bypass_devices")
CONSUMERS = ("scorer", "archiver")


class GameDay:
    def __init__(self, session, cell="a", standby="c", zone="T01", scope="zone", gap_s=60, recover_s=180, poll_s=5):
        self.cell, self.standby, self.zone, self.scope = cell, standby, zone, scope
        self.gap_s, self.recover_s, self.poll_s = gap_s, recover_s, poll_s
        ddb = session.resource("dynamodb")
        self.zones, self.states, self.alerts = (ddb.Table(f"{PREFIX}-{t}") for t in ("zones", "zone-state", "alerts"))
        self.iot, self.lam = session.client("iot"), session.client("lambda")
        endpoint = self.iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
        self.iotdata = session.client("iot-data", endpoint_url=f"https://{endpoint}")
        self.timeline, self.moved, self.sleep, self.clock = [], [], time.sleep, time.time

    def log(self, step, **details):
        self.timeline.append({"t": round(self.clock(), 1), "step": step, **details})
        print(f"{step}: {json.dumps(details)}", flush=True)

    # ---- the cell ----

    def mappings(self):
        out = []
        for name in CONSUMERS:
            fn = f"{PREFIX}-{self.cell}-{name}"
            out += [m["UUID"] for m in self.lam.list_event_source_mappings(FunctionName=fn)["EventSourceMappings"]]
        return out

    def set_cell(self, enabled):
        for uuid in self.mappings():
            self.lam.update_event_source_mapping(UUID=uuid, Enabled=enabled)
        for rule in RULES:
            name = f"{PREFIX}_{self.cell}_{rule}"
            (self.iot.enable_topic_rule if enabled else self.iot.disable_topic_rule)(ruleName=name)

    # ---- the zone(s) ----

    def zone_ids(self):
        if self.scope == "zone":
            return [self.zone]
        items, kwargs = [], {"FilterExpression": Attr("owner_cell").eq(self.cell)}
        while True:
            page = self.zones.scan(**kwargs)
            items += [z["zone_id"] for z in page["Items"]]
            if "LastEvaluatedKey" not in page:
                return sorted(items)
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def move(self, zone_ids, to):
        for zid in zone_ids:
            self.zones.update_item(Key={"zone_id": zid}, UpdateExpression="SET owner_cell = :o", ExpressionAttributeValues={":o": to})

    def danger(self):
        return float(self.zones.get_item(Key={"zone_id": self.zone})["Item"]["danger_level_m"])

    def publish(self):
        self.iotdata.publish(topic=f"sm/obs/{self.zone}", qos=1, payload=json.dumps({
            "source_class": "gauge", "source_id": "gameday", "ts": self.clock(), "drill": True, "level_m": self.danger() + 0.3}))

    def alerts_since(self, since):
        wanted = Attr("zone_id").eq(self.zone) & Attr("sk").eq("META") & Attr("issued_at").gte(int(since))
        return sorted(self.alerts.scan(FilterExpression=wanted)["Items"], key=lambda a: a["issued_at"])

    def wait_for_alert(self, since, timeout):
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            found = self.alerts_since(since)
            if found:
                return found[0]
            self.sleep(self.poll_s)
        return None

    # ---- the run ----

    def run(self):
        ok = True
        self.states.delete_item(Key={"zone_id": self.zone})
        zone_ids = self.zone_ids()
        try:
            t0 = self.clock()
            self.set_cell(False)
            self.log("cell lost", cell=self.cell, rules=len(RULES), consumers=len(CONSUMERS))
            self.sleep(10)  # let disabled mappings settle
            self.publish()
            leaked = self.wait_for_alert(t0, self.gap_s)
            self.log("gap", alerted=bool(leaked))
            if leaked:
                ok = False  # the lost cell still alerted: the loss was not real
            t_failover = self.clock()
            self.move(zone_ids, self.standby)
            self.moved = zone_ids
            self.log("failover", to=self.standby, zones=len(zone_ids))
            self.publish()
            alert = self.wait_for_alert(t_failover, self.recover_s)
            if alert:
                seconds = round(int(alert["issued_at"]) - t_failover, 1)
                self.log("recovered", alert_id=alert["alert_id"], cell=alert.get("cell"), seconds=seconds)
                ok = ok and alert.get("cell") == self.standby
            else:
                self.log("not recovered", waited_s=self.recover_s)
                ok = False
        finally:
            self.restore()
        return ok

    def restore(self):
        self.set_cell(True)
        if self.moved:
            self.move(self.moved, self.cell)
        self.states.delete_item(Key={"zone_id": self.zone})
        self.log("restored", cell=self.cell, zones=len(self.moved))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--cell", default="a")
    p.add_argument("--standby", default="c")
    p.add_argument("--zone", default="T01", help="zone used for the readings (a test zone by default)")
    p.add_argument("--scope", choices=["zone", "cell"], default="zone")
    args = p.parse_args(argv)
    day = GameDay(boto3.session.Session(region_name=REGION), args.cell, args.standby, args.zone, args.scope)
    ok = day.run()
    print("\nstep            seconds since start")
    start = day.timeline[0]["t"] if day.timeline else 0
    for e in day.timeline:
        print(f"{e['step']:<16}{e['t'] - start:>8.1f}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
