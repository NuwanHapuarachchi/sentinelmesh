"""Burst load test for the detection path.

IoT Core allows ~100 publishes/s per connection, so one CI runner can't drive the scenario's
4,000 readings/s through MQTT. Instead this writes synthetic gauge readings straight into both
cells' observation tables (exactly what the IoT rules do), using hidden load zones L01-L80,
then reports how far behind the scorers fell.

    python tests/load/burst.py [rate_per_s] [seconds]
"""

import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import boto3

REGION = "ap-southeast-2"
RATE = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
SECONDS = int(sys.argv[2]) if len(sys.argv) > 2 else 60
ZONES = [f"L{i:02d}" for i in range(1, 81)]

ddb = boto3.resource("dynamodb", region_name=REGION)
cw = boto3.client("cloudwatch", region_name=REGION)
tables = {"a": ddb.Table("sentinelmesh-a-observations"), "b": ddb.Table("sentinelmesh-b-observations")}


def item(zone):
    now = time.time()
    return {
        "zone_id": zone, "sk": f"{int(now * 1000)}#gauge#{uuid.uuid4()}", "rx_ms": int(now * 1000),
        "expires_at": int(now) + 3600, "source_class": "gauge", "source_id": "loadtest", "ts": Decimal(str(round(now, 3))),
        "level_m": Decimal("1.0"), "drill": True,
    }


def write_second(cell, count):
    zones = ZONES[:40] if cell == "a" else ZONES[40:]
    with tables[cell].batch_writer() as batch:
        for i in range(count):
            batch.put_item(Item=item(zones[i % len(zones)]))


def main():
    start = datetime.now(UTC)
    written = 0
    with ThreadPoolExecutor(max_workers=32) as pool:
        for second in range(SECONDS):
            t0 = time.time()
            per_worker = RATE // 16
            futures = [pool.submit(write_second, "a" if w % 2 else "b", per_worker) for w in range(16)]
            for f in futures:
                f.result()
            written += per_worker * 16
            lag = time.time() - t0
            print(f"second {second + 1}: wrote {per_worker * 16} readings in {lag:.2f}s", flush=True)
            time.sleep(max(0, 1 - lag))

    time.sleep(90)  # let metrics land
    end = datetime.now(UTC) + timedelta(minutes=1)
    print(f"\nwrote {written} readings over {SECONDS}s (target {RATE}/s)")
    for cell in ("a", "b"):
        stats = cw.get_metric_statistics(
            Namespace="SentinelMesh", MetricName="ScoreLatencyMs", Dimensions=[{"Name": "Cell", "Value": cell}],
            StartTime=start, EndTime=end, Period=60, ExtendedStatistics=["p50", "p99"],
        )["Datapoints"]
        worst = max(stats, key=lambda d: d["ExtendedStatistics"]["p99"], default=None)
        if worst:
            print(f"cell {cell}: scoring latency p50 {worst['ExtendedStatistics']['p50']:.0f} ms, "
                  f"p99 {worst['ExtendedStatistics']['p99']:.0f} ms (worst minute)")
        else:
            print(f"cell {cell}: no latency datapoints yet")


if __name__ == "__main__":
    main()
