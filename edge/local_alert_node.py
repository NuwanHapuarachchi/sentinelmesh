"""Local Alert Node: warns its district from the district emergency office, with or without the cloud.

  - Keeps the district's pre-staged bundle (zones, thresholds, recipients) on disk, refreshed from the cloud
    every minute while it can reach it.
  - Listens on the LAN for field station readings (edge/field_gateway.py --node http://<this host>:8080).
  - Listens to its district's cloud alerts (sm/alerts/<district>) over MQTT, so it knows when the cloud has
    already warned a zone.
  - On a reading at or above a zone's danger level it defers to the cloud for a grace period, then issues
    itself; with the cloud unreachable it issues at once (edge/node_core.py).
  - Issuing means: SMS to the zone's recipients through a LAN SMS gateway (common/sms.py HTTP adapter, e.g.
    an Android phone running an SMS gateway app, or a USB GSM modem's HTTP API) and a local "cell broadcast"
    that phones on the LAN pick up (this server also serves the phone page).
  - Every decision goes into a hash-chained local log, uploaded into the cloud's decision chain when the
    connection returns.

    python edge/local_alert_node.py --thing sentinelmesh-node-kegalle --sms-url http://192.168.43.1:8082/send
    open http://<this host>:8080/ for the status page; phones open http://<this host>:8080/phone.html

Credentials: the node's secret (sentinelmesh/devices/<thing>, from Terraform) is fetched once with your AWS
credentials and kept in edge/state/<thing>/ (git-ignored); after that the node runs on its certificate alone.
Requires Python 3.11+ and boto3.
"""

import argparse
import html
import http.client
import json
import os
import secrets
import socket
import ssl
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from edge import mqtt, node_core
except ImportError:  # run as a script from the edge folder
    import mqtt
    import node_core

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services"))
from common import chain, sms  # noqa: E402

ROOT = Path(__file__).resolve().parent
WEB = ROOT.parent / "web"
PHONE_FILES = {"/phone.html": "text/html; charset=utf-8", "/phone.js": "application/javascript",
               "/phone.css": "text/css", "/zones.geojson": "application/geo+json"}
CLOUD_SILENCE_S = 120  # no MQTT traffic or successful sync for this long = the cloud is gone


def temporary_credentials(endpoint, role_alias, thing, context, timeout=10):
    """Exchange the node certificate for short-lived AWS credentials (IoT credentials provider)."""
    conn = http.client.HTTPSConnection(endpoint, 443, context=context, timeout=timeout)
    try:
        conn.request("GET", f"/role-aliases/{role_alias}/credentials", headers={"x-amzn-iot-thingname": thing})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise OSError(f"credentials provider returned {resp.status}: {body[:200]!r}")
        return parse_credentials(body)
    finally:
        conn.close()


def parse_credentials(body):
    c = json.loads(body)["credentials"]
    return {"aws_access_key_id": c["accessKeyId"], "aws_secret_access_key": c["secretAccessKey"],
            "aws_session_token": c["sessionToken"]}


class Node:
    def __init__(self, cfg, gateway_env=None, gateway_secret=None, status="Exercise", grace_s=60, pin=None,
                 state_dir=None, sender=None, clock=time.time):
        self.cfg, self.status, self.clock = cfg, status, clock
        self.thing, self.slug = cfg["thing"], cfg["slug"]
        state = Path(state_dir or ROOT / "state" / self.thing)
        self.store = node_core.BundleStore(state / "bundle.json")
        self.log = node_core.LocalLog(state / "decisions.jsonl")
        self.decider = node_core.Decider(grace_s=grace_s)
        self.gateway_env, self.gateway_secret = gateway_env, gateway_secret or {}
        self.sender = sender or sms.send_http
        self.pin = pin or secrets.token_hex(3)
        self.broadcasts = {}  # zone -> local broadcast object (the phone page polls cb/<zone>.json)
        self.last_cloud_contact = 0.0
        self.lock = threading.Lock()
        self.events = []

    # ---- state ----

    def cloud_alive(self):
        return self.clock() - self.last_cloud_contact < CLOUD_SILENCE_S

    def note(self, text):
        line = f"{time.strftime('%H:%M:%S')} {text}"
        print(line, flush=True)
        self.events = ([line] + self.events)[:30]

    # ---- inputs ----

    def on_cloud_alert(self, message):
        with self.lock:
            self.last_cloud_contact = self.clock()
            self.decider.cloud_alert(message["zone_id"], message["tier"], self.clock())
        self.note(f"cloud issued {message['tier']} for {message['zone_id']} ({message.get('alert_id')})")

    def on_reading(self, reading):
        """A field station reading from the LAN. Returns the decision."""
        if reading.get("source_class", "gauge") != "gauge":
            return "ignored"
        level = reading.get("level_m")
        if isinstance(level, bool) or not isinstance(level, (int, float)):
            return "invalid"
        with self.lock:
            zone = self.store.zone(reading.get("zone_id"))
            if zone is None:
                return "unknown zone"
            decision = self.decider.reading(zone, float(level), self.clock(), self.cloud_alive())
        if decision == "issue":
            self.issue(zone["zone_id"], "imminent", reason=f"local hard threshold: {reading.get('level_m')} m "
                       f"(danger {zone['danger_level_m']} m), cloud {'silent' if self.cloud_alive() else 'unreachable'}")
        elif decision == "wait":
            self.note(f"{zone['zone_id']} at {reading.get('level_m')} m: waiting {self.decider.grace_s}s for the cloud")
        return decision

    def tick(self):
        """Issue for zones whose grace has run out with no cloud alert."""
        with self.lock:
            due = self.decider.due(self.clock(), self.cloud_alive())
        for zone_id in due:
            self.issue(zone_id, "imminent", reason="no cloud alert within the grace period")

    # ---- output ----

    def issue(self, zone_id, tier, reason, officer=None):
        now = self.clock()
        with self.lock:
            zone = self.store.zone(zone_id)
            if zone is None:
                raise ValueError(f"zone {zone_id} is not in this district's bundle")
            alert = node_core.local_alert(self.slug, zone, tier, self.status, now)
            self.decider.issued(zone_id, now)
            self.broadcasts[zone_id] = {k: v for k, v in alert.items() if k != "cap_xml"}
            recipients = self.store.recipients(zone_id)
        sent = failed = 0
        for r in recipients:
            if not r.get("msisdn") or not self.gateway_env:
                continue  # simulated handsets get the local broadcast
            text = alert["texts"].get(r.get("lang", "en"), alert["texts"]["en"])
            result = self.sender(r["msisdn"], text, f"{alert['alert_id']}|{r['subscriber_id']}", self.gateway_secret, env=self.gateway_env)
            sent, failed = sent + (result["status"] == "SENT"), failed + (result["status"] != "SENT")
        record = {"alert_id": alert["alert_id"], "zone_id": zone_id, "tier": tier, "status": self.status, "reason": reason,
                  "officer": officer, "issued_at": int(now), "cloud_alive": self.cloud_alive(), "sms_sent": sent,
                  "sms_failed": failed, "recipients": len(recipients), "bundle_version": (self.store.bundle or {}).get("version"),
                  "texts": alert["texts"], "cap_xml": alert["cap_xml"], "node": self.thing}
        with self.lock:
            self.log.append(record)
        self.note(f"ISSUED {tier} for {zone_id}: {sent} SMS sent, {failed} failed; {reason}")
        return record

    # ---- cloud sync ----

    def sync(self, session):
        """Refresh the bundle and upload unsent local decisions into the cloud chain. session: a boto3 Session."""
        s3 = session.client("s3")
        raw = s3.get_object(Bucket=self.cfg["evidence_bucket"], Key=f"edge/{self.thing}/bundle.json")["Body"].read()
        with self.lock:
            changed = self.store.update(raw)
            self.last_cloud_contact = self.clock()
        if changed:
            self.note(f"bundle {self.store.bundle['version']}: {len(self.store.bundle['zones'])} zones, "
                      f"{len(self.store.bundle['recipients'])} recipients")
        return self.upload(s3, session.resource("dynamodb").Table(self.cfg["meta_table"]))

    def upload(self, s3, meta):
        uploaded = 0
        for entry in self.log.pending():
            record = entry["record"]
            key = f"decisions/edge/{self.thing}/{entry['seq']:06d}-{record['alert_id']}.json"
            raw = json.dumps(entry, sort_keys=True, ensure_ascii=False).encode("utf-8")
            try:
                s3.put_object(Bucket=self.cfg["decisions_bucket"], Key=key, Body=raw, IfNoneMatch="*", ContentType="application/json")
            except Exception as exc:  # botocore ClientError; 412 = an earlier attempt already uploaded it
                if getattr(exc, "response", {}).get("Error", {}).get("Code") != "PreconditionFailed":
                    raise
            # Chain it even if it was already there: a duplicate chain entry is harmless, a missing one is a gap.
            chain.append(s3, self.cfg["decisions_bucket"], meta, key, raw, at=record["issued_at"])
            self.log.mark_uploaded(entry["seq"])
            uploaded += 1
        if uploaded:
            self.note(f"uploaded {uploaded} local decisions to the cloud decision chain")
        return uploaded

    def status_doc(self):
        now = self.clock()
        bundle = self.store.bundle or {}
        return {"node": self.thing, "district": bundle.get("district"), "cloud_alive": self.cloud_alive(),
                "bundle_version": bundle.get("version"), "bundle_age_s": self.store.age_s(now),
                "zones": [z["zone_id"] for z in bundle.get("zones", [])], "pending": sorted(self.decider.pending),
                "issued": sorted(self.decider.local_alerts), "log_ok": not self.log.verify(), "events": self.events}


# ---------- LAN HTTP service ----------

def make_handler(node):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, content_type="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def body(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
            if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
                return {k: v[0] for k, v in parse_qs(raw).items()}
            return json.loads(raw or "{}")

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/status":
                return self.send(200, node.status_doc())
            if path.startswith("/cb/") and path.endswith(".json"):
                b = node.broadcasts.get(path[4:-5])
                return self.send(200, b) if b else self.send(404, {"error": "no broadcast"})
            if path == "/config.json":
                return self.send(200, {"api_url": ""})
            if path in PHONE_FILES:
                return self.send(200, (WEB / path[1:]).read_bytes(), PHONE_FILES[path])
            if path == "/":
                return self.send(200, status_page(node).encode("utf-8"), "text/html; charset=utf-8")
            return self.send(404, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                body = self.body()
                if path == "/reading":
                    return self.send(200, {"decision": node.on_reading(body)})
                if path == "/issue":
                    if body.get("pin") != node.pin and self.headers.get("X-Officer-Pin") != node.pin:
                        return self.send(403, {"error": "officer PIN required"})
                    record = node.issue(body["zone_id"], body.get("tier", "imminent"), reason="issued manually",
                                        officer=body.get("officer") or "district officer")
                    return self.send(200, {"alert_id": record["alert_id"], "sms_sent": record["sms_sent"]})
            except (ValueError, KeyError) as exc:
                return self.send(400, {"error": str(exc)})
            return self.send(404, {"error": "not found"})

    return Handler


def status_page(node):
    s = node.status_doc()
    cloud = "reachable" if s["cloud_alive"] else "UNREACHABLE - this node is warning the district on its own"
    zones = "".join(f"<option>{html.escape(z)}</option>" for z in s["zones"])
    events = "".join(f"<li>{html.escape(e)}</li>" for e in s["events"]) or "<li>nothing yet</li>"
    age = "never" if s["bundle_age_s"] is None else f"{int(s['bundle_age_s'])} s old"
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(str(s['district']))} Local Alert Node</title><meta http-equiv="refresh" content="5">
<style>body{{font:16px system-ui,sans-serif;margin:24px;max-width:760px}}b.bad{{color:#b00020}}</style>
<h1>{html.escape(str(s['district']))} Local Alert Node</h1>
<p>Cloud: <b class="{'' if s['cloud_alive'] else 'bad'}">{cloud}</b><br>Bundle {s['bundle_version']} ({age}),
{len(s['zones'])} zones. Decision log {'intact' if s['log_ok'] else 'BROKEN'}.</p>
<form method="post" action="/issue"><h2>Issue manually</h2>
<select name="zone_id">{zones}</select> <select name="tier"><option>imminent</option><option>warning</option></select>
<input name="pin" placeholder="officer PIN" size="8"> <button>Issue now</button></form>
<h2>Events</h2><ul>{events}</ul><p><a href="/phone.html">Phone page (local broadcast)</a></p>"""


# ---------- cloud link ----------

def run_cloud(node, cfg, context, boto3, stop):
    """Bundle sync every minute (with fresh credentials) and the MQTT alert subscription, reconnecting forever."""
    def sync_loop():
        while not stop.is_set():
            try:
                creds = temporary_credentials(cfg["credentials_endpoint"], cfg["role_alias"], cfg["thing"], context)
                node.sync(boto3.session.Session(region_name=cfg["region"], **creds))
            except Exception as exc:
                node.note(f"cloud sync failed: {exc}")
            stop.wait(60)

    def mqtt_loop():
        while not stop.is_set():
            client = None
            try:
                client = mqtt.Client(cfg["endpoint"], cfg["thing"], context, keepalive=60).connect()
                client.subscribe(f"sm/alerts/{cfg['slug']}")
                client.sock.settimeout(20)
                node.last_cloud_contact = node.clock()
                node.note("connected to the cloud alert topic")
                while not stop.is_set():
                    try:
                        kind, flags, body = mqtt.read_packet(client.sock)
                    except TimeoutError:
                        client.ping()
                        if node.clock() - node.last_cloud_contact > CLOUD_SILENCE_S:
                            raise mqtt.MqttError("no reply from the cloud") from None
                        continue
                    node.last_cloud_contact = node.clock()
                    if kind == mqtt.PUBLISH:
                        topic, payload, qos, pid = mqtt.parse_publish(flags, body)
                        if qos == 1:
                            client.sock.sendall(mqtt.packet(mqtt.PUBACK, 0, pid.to_bytes(2, "big")))
                        node.on_cloud_alert(json.loads(payload))
            except (OSError, mqtt.MqttError, ValueError) as exc:
                node.note(f"cloud alert topic lost: {exc}")
            finally:
                if client:
                    client.close()
            stop.wait(10)

    for target in (sync_loop, mqtt_loop):
        threading.Thread(target=target, daemon=True).start()


def load_config(thing, region):
    """The node's secret, cached in edge/state/<thing>/node.json after the first fetch."""
    folder = ROOT / "state" / thing
    cached = folder / "node.json"
    if cached.exists():
        cfg = json.loads(cached.read_text(encoding="utf-8"))
    else:
        import boto3

        cfg = json.loads(boto3.client("secretsmanager", region_name=region)
                         .get_secret_value(SecretId=f"sentinelmesh/devices/{thing}")["SecretString"])
        folder.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(cfg), encoding="utf-8")
    (folder / "cert.pem").write_text(cfg["certificate_pem"], encoding="utf-8")
    (folder / "key.pem").write_text(cfg["private_key_pem"], encoding="utf-8")
    return cfg, folder


def main(argv=None):
    p = argparse.ArgumentParser(description="SentinelMesh Local Alert Node")
    p.add_argument("--thing", required=True, help="e.g. sentinelmesh-node-kegalle")
    p.add_argument("--region", default="ap-southeast-2")
    p.add_argument("--listen", default="0.0.0.0:8080")
    p.add_argument("--grace", type=int, default=60, help="seconds to wait for the cloud's alert before issuing")
    p.add_argument("--status", choices=["Exercise", "Actual"], default="Exercise", help="CAP status of local alerts")
    p.add_argument("--pin", help="officer PIN for manual issue (random if not given)")
    p.add_argument("--offline", action="store_true", help="never contact the cloud (works from the cached bundle)")
    p.add_argument("--sms-url", help="LAN SMS gateway send URL; SMS_HTTP_* style options below")
    p.add_argument("--sms-format", default="json")
    p.add_argument("--sms-to-field", default="to")
    p.add_argument("--sms-text-field", default="message")
    p.add_argument("--sms-auth", default="bearer")
    p.add_argument("--sms-key", default="", help="gateway API key (or set SMS_GATEWAY_KEY)")
    args = p.parse_args(argv)

    cfg, folder = load_config(args.thing, args.region)
    gateway = None
    if args.sms_url:
        gateway = {"SMS_HTTP_URL": args.sms_url, "SMS_HTTP_FORMAT": args.sms_format, "SMS_HTTP_TO_FIELD": args.sms_to_field,
                   "SMS_HTTP_TEXT_FIELD": args.sms_text_field, "SMS_HTTP_AUTH": args.sms_auth}
    node = Node(cfg, gateway_env=gateway, gateway_secret={"api_key": args.sms_key or os.environ.get("SMS_GATEWAY_KEY", "")},
                status=args.status, grace_s=args.grace, pin=args.pin)
    stop = threading.Event()
    if not args.offline:
        import boto3

        context = ssl.create_default_context()
        context.load_cert_chain(str(folder / "cert.pem"), str(folder / "key.pem"))
        run_cloud(node, cfg, context, boto3, stop)

    def ticker():
        while not stop.is_set():
            try:
                node.tick()
            except Exception:
                traceback.print_exc()
            stop.wait(2)

    threading.Thread(target=ticker, daemon=True).start()
    host, port = args.listen.rsplit(":", 1)
    server = ThreadingHTTPServer((host, int(port)), make_handler(node))
    lan = socket.gethostbyname(socket.gethostname())
    print(f"{cfg['district']} Local Alert Node on http://{lan}:{port}/  (officer PIN {node.pin}; local alerts are {args.status})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
