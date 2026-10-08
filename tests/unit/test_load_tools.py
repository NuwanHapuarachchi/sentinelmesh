import importlib.util
import sys
from pathlib import Path


def load(name):
    path = Path(__file__).resolve().parents[1] / "load" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, [str(path)]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
    return mod


def test_dispatch_burst_seeds_and_removes_exactly_its_subscribers(stack):
    burst = load("dispatch_burst")
    table = stack.table("subscribers")
    table.put_item(Item={"subscriber_id": "real-1", "zone_id": "L01"})
    burst.seed(table, 30)
    rows = table.scan()["Items"]
    assert len(rows) == 31 and {r["lang"] for r in rows if r["subscriber_id"] != "real-1"} == {"en", "si", "ta"}
    burst.clean(table, 30)
    assert [r["subscriber_id"] for r in table.scan()["Items"]] == ["real-1"]


def test_dispatch_burst_timings():
    burst = load("dispatch_burst")
    meta = {"audience": 10000, "sent": 10000, "batches": 200, "planned_at": 1012}
    assert burst.summarise(meta, 1000.0, 1100.0) == {"audience": 10000, "sent": 10000, "batches": 200,
                                                    "reading_to_planned_s": 12.0, "drain_s": 88.0, "reading_to_last_send_s": 100.0}
