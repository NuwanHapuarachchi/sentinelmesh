"""The GameDay script's orchestration, against a simulated pipeline and a fake clock."""

import pytest

from scripts import gameday as gd


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class World:
    """Cell a, standby c and one zone. A reading alerts (12 s later) if the zone's owner cell is up."""

    def __init__(self, clock, cell_alerts_while_down=False, standby_works=True):
        self.clock, self.owner = clock, {"T01": "a", "Z07": "a", "Z50": "b"}
        self.enabled_rules, self.enabled_mappings = set(), set()
        self.calls, self.alerts = [], []
        self.cell_alerts_while_down, self.standby_works = cell_alerts_while_down, standby_works

    def cell_up(self, cell):
        return cell == "c" or {f"sentinelmesh_{cell}_route"} <= self.enabled_rules or self.cell_alerts_while_down

    def reading(self, zone):
        owner = self.owner[zone]
        if (owner == "c" and self.standby_works) or (owner != "c" and self.cell_up(owner)):
            self.alerts.append({"alert_id": f"A{len(self.alerts)}", "zone_id": zone, "cell": owner, "issued_at": self.clock.t + 12})


class FakeLambda:
    def __init__(self, world):
        self.world = world

    def list_event_source_mappings(self, FunctionName):
        return {"EventSourceMappings": [{"UUID": f"{FunctionName}-uuid"}]}

    def update_event_source_mapping(self, UUID, Enabled):
        self.world.calls.append(("mapping", UUID, Enabled))


class FakeIot:
    def __init__(self, world):
        self.world = world

    def enable_topic_rule(self, ruleName):
        self.world.enabled_rules.add(ruleName)
        self.world.calls.append(("rule", ruleName, True))

    def disable_topic_rule(self, ruleName):
        self.world.enabled_rules.discard(ruleName)
        self.world.calls.append(("rule", ruleName, False))


class FakeIotData:
    def __init__(self, world):
        self.world = world

    def publish(self, topic, qos, payload):
        self.world.reading(topic.rsplit("/", 1)[1])


class Table:
    def __init__(self, world):
        self.world = world

    def get_item(self, Key):
        return {"Item": {"zone_id": Key["zone_id"], "danger_level_m": "4.5"}}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues):
        self.world.owner[Key["zone_id"]] = ExpressionAttributeValues[":o"]

    def delete_item(self, Key):
        self.world.calls.append(("reset", Key["zone_id"]))

    def scan(self, FilterExpression):
        return {"Items": [{"zone_id": z} for z, c in self.world.owner.items() if c == "a"]}


def day(world, clock, scope="zone"):
    g = gd.GameDay.__new__(gd.GameDay)
    g.cell, g.standby, g.zone, g.scope, g.gap_s, g.recover_s, g.poll_s = "a", "c", "T01", scope, 60, 180, 5
    g.zones = g.states = g.alerts = Table(world)
    g.iot, g.lam, g.iotdata = FakeIot(world), FakeLambda(world), FakeIotData(world)
    g.timeline, g.moved, g.sleep, g.clock = [], [], clock.sleep, clock
    g.alerts_since = lambda since: [a for a in world.alerts if a["issued_at"] >= since and a["issued_at"] <= clock.t]
    world.enabled_rules = {f"sentinelmesh_a_{r}" for r in gd.RULES}
    return g


def test_gap_then_recovery_through_the_standby_then_everything_restored():
    clock = Clock()
    world = World(clock)
    g = day(world, clock)
    assert g.run() is True
    steps = {e["step"]: e for e in g.timeline}
    assert steps["gap"]["alerted"] is False
    assert steps["recovered"]["cell"] == "c" and 0 < steps["recovered"]["seconds"] <= 20
    assert world.owner == {"T01": "a", "Z07": "a", "Z50": "b"}  # handed back
    assert world.enabled_rules == {f"sentinelmesh_a_{r}" for r in gd.RULES}
    disabled = [c for c in world.calls if c[0] == "mapping" and c[2] is False]
    assert {c[1] for c in disabled} == {"sentinelmesh-a-scorer-uuid", "sentinelmesh-a-archiver-uuid"}


def test_a_lost_cell_that_still_alerts_fails_the_gameday():
    clock = Clock()
    world = World(clock, cell_alerts_while_down=True)
    assert day(world, clock).run() is False


def test_no_recovery_fails_but_still_restores():
    clock = Clock()
    world = World(clock, standby_works=False)
    g = day(world, clock)
    assert g.run() is False
    assert [e["step"] for e in g.timeline][-2:] == ["not recovered", "restored"]
    assert world.owner["T01"] == "a" and "sentinelmesh_a_route" in world.enabled_rules


def test_a_crash_mid_run_still_restores():
    clock = Clock()
    world = World(clock)
    g = day(world, clock)

    def broken():
        raise RuntimeError("IoT unreachable")

    g.publish = broken
    with pytest.raises(RuntimeError):
        g.run()
    assert world.enabled_rules == {f"sentinelmesh_a_{r}" for r in gd.RULES}
    assert g.timeline[-1]["step"] == "restored"


def test_cell_scope_moves_every_zone_of_the_lost_cell():
    clock = Clock()
    world = World(clock)
    g = day(world, clock, scope="cell")
    assert g.run() is True
    assert next(e for e in g.timeline if e["step"] == "failover")["zones"] == 2  # T01 and Z07, not cell b's Z50
