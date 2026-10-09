"""Local Alert Node logic, kept free of networking so it can be tested: the pre-staged bundle, the
issue-or-defer decision, the local alert itself, and the node's hash-chained decision log.

The decision is fail-toward-alerting at the edge. A gauge reading at or above its zone's danger level:
  - is "covered" if the cloud already issued an imminent alert for that zone in the last 6 hours;
  - is issued at once if the cloud is unreachable;
  - otherwise waits a grace period (the cloud path normally takes 10-30 s) and is issued if no cloud
    alert for the zone has arrived by then.
A zone the node has itself alerted is not alerted again for 6 hours.
"""

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services"))
from common import cap, messages  # noqa: E402  (the same templates and CAP builder as the cloud)

COVER_S = 6 * 3600
GENESIS = "0" * 64


class BundleStore:
    """The last good bundle, kept on disk so the node can work from it after a restart with no cloud."""

    def __init__(self, path):
        self.path = Path(path)
        self.bundle = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else None

    def update(self, raw):
        bundle = json.loads(raw)
        if not isinstance(bundle.get("zones"), list) or not bundle["zones"] or "recipients" not in bundle:
            raise ValueError("not a district bundle")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic: a crash never leaves half a bundle
        changed = (self.bundle or {}).get("version") != bundle.get("version")
        self.bundle = bundle
        return changed

    def zone(self, zone_id):
        return next((z for z in (self.bundle or {}).get("zones", []) if z["zone_id"] == zone_id), None)

    def recipients(self, zone_id):
        return [r for r in (self.bundle or {}).get("recipients", []) if r["zone_id"] == zone_id]

    def age_s(self, now):
        return None if not self.bundle else now - self.bundle.get("generated_at", 0)


class Decider:
    def __init__(self, grace_s=60, cover_s=COVER_S):
        self.grace_s, self.cover_s = grace_s, cover_s
        self.cloud_alerts = {}  # zone -> (tier, at)
        self.local_alerts = {}  # zone -> at
        self.pending = {}  # zone -> (since, reading)

    def cloud_alert(self, zone_id, tier, at):
        self.cloud_alerts[zone_id] = (tier, at)
        if tier == "imminent":
            self.pending.pop(zone_id, None)

    def covered(self, zone_id, now):
        tier, at = self.cloud_alerts.get(zone_id, (None, 0))
        return tier == "imminent" and now - at < self.cover_s

    def reading(self, zone, value, now, cloud_alive):
        """One gauge reading -> 'below', 'covered', 'already', 'wait' or 'issue'."""
        zone_id = zone["zone_id"]
        if value is None or value < zone["danger_level_m"]:
            self.pending.pop(zone_id, None)  # the water went back down before the grace ran out
            return "below"
        if self.covered(zone_id, now):
            return "covered"
        if now - self.local_alerts.get(zone_id, -self.cover_s) < self.cover_s:
            return "already"
        if not cloud_alive:
            return "issue"
        since, _ = self.pending.setdefault(zone_id, (now, value))
        return "issue" if now - since >= self.grace_s else "wait"

    def due(self, now, cloud_alive):
        """Zones whose grace has run out (or all pending ones, if the cloud has gone) and are still uncovered."""
        out = []
        for zone_id, (since, _) in list(self.pending.items()):
            if self.covered(zone_id, now):
                self.pending.pop(zone_id)
            elif not cloud_alive or now - since >= self.grace_s:
                out.append(zone_id)
        return out

    def issued(self, zone_id, now):
        self.local_alerts[zone_id] = now
        self.pending.pop(zone_id, None)


def local_alert(node, zone, tier, status, now):
    """The alert a node issues on its own: same templates and CAP format as the cloud, marked as local."""
    stamp = datetime.fromtimestamp(now, UTC).strftime("%Y%m%d%H%M%S")
    alert_id = f"LOCAL-{node}-{zone['zone_id']}-{stamp}"
    texts = messages.render_all(tier, zone["hazard"], zone["zone_id"], stamp[-6:])
    xml = cap.build(alert_id, zone, tier, zone["hazard"], now, status=status)
    return {"type": "alert", "alert_id": alert_id, "zone_id": zone["zone_id"], "tier": tier, "hazard": zone["hazard"],
            "status": status, "texts": texts, "origin": "local-node", "broadcast_at": int(now), "cap_xml": xml}


class LocalLog:
    """Append-only, hash-chained JSON lines: every record carries the hash of the line before it, so the
    node's own history is tamper-evident until it is uploaded into the cloud chain."""

    def __init__(self, path):
        self.path = Path(path)
        self.uploaded_path = self.path.with_suffix(".uploaded")

    def lines(self):
        if not self.path.exists():
            return []
        return [line for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def append(self, record):
        lines = self.lines()
        prev = hashlib.sha256(lines[-1].encode()).hexdigest() if lines else GENESIS
        line = json.dumps({"seq": len(lines) + 1, "prev_sha256": prev, "record": record}, sort_keys=True, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return json.loads(line)

    def verify(self):
        """List of problems (empty when intact)."""
        problems, prev = [], GENESIS
        for i, line in enumerate(self.lines(), 1):
            entry = json.loads(line)
            if entry["seq"] != i:
                problems.append(f"line {i}: sequence {entry['seq']}")
            if entry["prev_sha256"] != prev:
                problems.append(f"line {i}: does not link to the line before")
            prev = hashlib.sha256(line.encode()).hexdigest()
        return problems

    def uploaded(self):
        return int(self.uploaded_path.read_text()) if self.uploaded_path.exists() else 0

    def mark_uploaded(self, seq):
        self.uploaded_path.write_text(str(seq))

    def pending(self):
        done = self.uploaded()
        return [json.loads(line) for line in self.lines()][done:]
