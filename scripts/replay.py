"""Replay recorded observations through the scoring rules (services/common/scoring.py) and print the tier
timeline the rules produce: what they would have done, minute by minute.

Use it after an event, to explain or audit a decision, and before changing a threshold or weight, to see what
the change would have done to real history ("thresholds versioned like code").

    python scripts/replay.py --zone Z07 --athena --since 2026-10-08T00:00 --until 2026-10-09T00:00
    python scripts/replay.py --zone Z07 --file kalu_ganga_2024.csv
    python scripts/replay.py --zone Z07 --file obs.jsonl --set ENTER.warning=0.6 --set WEIGHTS.report=0.2

Observations come from the evidence lake through Athena (the observations table over s3://...-evidence) or
from a file: JSON lines or CSV with a header, one observation per row, using the observation fields
(ts in epoch seconds or ISO 8601, source_class, level_m / rain_24h_mm / tilt_deg / fc_rain_24h_mm,
report_kind / report_sev / reporter_hash). Between observations the replay runs the same one-minute
watchdog the cells run, so decay, hold-down and silence behave as they did live.
"""

import argparse
import contextlib
import csv
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services"))
from common import scoring, zonestate  # noqa: E402

ZONES = ROOT / "terraform" / "app" / "data" / "zones.json"
NUMERIC = ("ts", "rx_ms", "level_m", "rain_24h_mm", "tilt_deg", "fc_rain_24h_mm", "report_sev")


def epoch(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def normalise(row):
    """One observation from any source: numbers as numbers, ts in epoch seconds, empty fields dropped."""
    out = {k: v for k, v in row.items() if v not in (None, "")}
    for k in NUMERIC:
        if k in out:
            out[k] = epoch(out[k]) if k == "ts" else float(out[k])
    if "report_sev" in out:
        out["report_sev"] = int(out["report_sev"])
    if "ts" not in out and "rx_ms" in out:
        out["ts"] = out["rx_ms"] / 1000
    return out


def read_file(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".csv":
        rows = list(csv.DictReader(text.splitlines()))
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return [normalise(r) for r in rows]


@contextlib.contextmanager
def overrides(settings):
    """--set NAME=value or --set NAME.key=value on the scoring module, undone afterwards."""
    undo = []
    try:
        for setting in settings or []:
            target, _, value = setting.partition("=")
            name, _, key = target.partition(".")
            if not hasattr(scoring, name):
                raise ValueError(f"scoring has no {name}")
            current = getattr(scoring, name)
            if key:
                undo.append(lambda c=current, k=key, v=current[key]: c.__setitem__(k, v))
                current[key] = float(value)
            else:
                undo.append(lambda n=name, v=current: setattr(scoring, n, v))
                setattr(scoring, name, type(current)(float(value)))
        yield
    finally:
        for fn in reversed(undo):
            fn()


def replay(zone, observations, tick_s=60, until=None):
    """Feed observations in time order, with watchdog ticks in between. Returns tier changes:
    [{"time", "tier", "from", "score", "reason", "agreeing", "blind"}]."""
    obs = sorted(observations, key=lambda o: o["ts"])
    if not obs:
        return []
    doc, tier, changes = zonestate.empty_doc(), "normal", []
    now = obs[0]["ts"]
    end = max(until or 0, obs[-1]["ts"])

    def evaluate(batch, at):
        nonlocal tier
        zonestate.merge(doc, batch, at)
        a, new_tier, _ = zonestate.evaluate(zone, doc, tier, at)
        if new_tier != tier:
            changes.append({"time": at, "tier": new_tier, "from": tier, "score": a.score, "reason": a.reason,
                            "agreeing": a.agreeing, "blind": a.blind})
            tier = new_tier

    i = 0
    while i < len(obs) or now <= end:
        batch = []
        while i < len(obs) and obs[i]["ts"] <= now:
            batch.append(obs[i])
            i += 1
        evaluate(batch, now)
        if i >= len(obs) and now >= end:
            break
        now = min(now + tick_s, obs[i]["ts"]) if i < len(obs) else now + tick_s
    return changes


def athena_observations(session, zone_id, since, until, workgroup="sentinelmesh-replay", database="sentinelmesh_evidence"):
    if not re.fullmatch(r"[A-Z]\d{2}", zone_id):
        raise ValueError("zone must look like Z07")
    days = sorted({datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d") for t in (since, until)})
    query = (f'SELECT * FROM "{database}"."observations" WHERE zone_id = \'{zone_id}\' '
             f"AND dt BETWEEN '{days[0]}' AND '{days[-1]}' AND rx_ms BETWEEN {int(since * 1000)} AND {int(until * 1000)} "
             "ORDER BY rx_ms")
    athena = session.client("athena")
    qid = athena.start_query_execution(QueryString=query, WorkGroup=workgroup)["QueryExecutionId"]
    while True:
        state = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        if state["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)
    if state["State"] != "SUCCEEDED":
        raise RuntimeError(f"Athena query {state['State']}: {state.get('StateChangeReason', '')}")
    rows, header, kwargs = [], None, {"QueryExecutionId": qid}
    while True:
        page = athena.get_query_results(**kwargs)
        for r in page["ResultSet"]["Rows"]:
            values = [c.get("VarCharValue") for c in r["Data"]]
            if header is None:
                header = values
            else:
                rows.append(normalise(dict(zip(header, values, strict=True))))
        if "NextToken" not in page:
            return rows
        kwargs["NextToken"] = page["NextToken"]


def zone_config(zone_id):
    zone = next((z for z in json.loads(ZONES.read_text(encoding="utf-8")) if z["zone_id"] == zone_id), None)
    if zone is None:
        raise SystemExit(f"unknown zone {zone_id}")
    return zone


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--zone", required=True)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file")
    src.add_argument("--athena", action="store_true")
    p.add_argument("--since", help="ISO time or epoch (Athena)")
    p.add_argument("--until", help="ISO time or epoch (Athena; also how long to keep ticking after the last reading)")
    p.add_argument("--set", action="append", default=[], help="override a scoring constant, e.g. ENTER.warning=0.6")
    p.add_argument("--tick", type=int, default=60)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    zone = zone_config(args.zone)
    if args.athena:
        import boto3

        since, until = epoch(args.since), epoch(args.until) or time.time()
        obs = athena_observations(boto3.session.Session(region_name="ap-southeast-2"), args.zone, since, until)
    else:
        obs = read_file(args.file)
    with overrides(args.set):
        changes = replay(zone, obs, args.tick, epoch(args.until))
    if args.json:
        print(json.dumps(changes, indent=1))
    else:
        print(f"{args.zone} ({zone['name']}): {len(obs)} observations, {len(changes)} tier changes"
              + (f" with {', '.join(args.set)}" if args.set else ""))
        for c in changes:
            when = datetime.fromtimestamp(c["time"], UTC).strftime("%Y-%m-%d %H:%M:%S")
            print(f"{when}Z  {c['from']:>9} -> {c['tier']:<9} score {c['score']:.2f}  {c['reason']}  {','.join(c['agreeing'])}"
                  + ("  BLIND" if c["blind"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
