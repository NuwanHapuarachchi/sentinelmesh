"""Simulated field station: a river gauge (or tilt meter) with its own X.509 identity.

It connects to AWS IoT Core as its thing (the certificate only allows that), publishes readings to
sm/dev/{zone}/{class} (the only topic its policy allows), and, when given a district node URL, also posts each
reading to the Local Alert Node over the LAN, so the district can still act with no cloud at all.
While the cloud is unreachable, readings are buffered to a local file and replayed on reconnect (the
"store and forward" a Greengrass field gateway gives in production).

    python edge/field_gateway.py --thing sentinelmesh-gauge-Z07 --profile rising
    python edge/field_gateway.py --thing sentinelmesh-gauge-Z07 --profile surge --node http://192.168.43.10:8080

Credentials: the station's secret in Secrets Manager (sentinelmesh/devices/<thing>, written by Terraform:
certificate, private key, IoT endpoint, zone, class and thresholds), fetched once with your AWS credentials and
kept in edge/state/<thing>/ (git-ignored) so the station can start offline; or --cert/--key/--endpoint/--zone
files and flags. Python 3.11+, standard library only (plus boto3 for the first fetch).
"""

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

try:
    from edge import mqtt
except ImportError:  # run as a script from the edge folder
    import mqtt

PROFILES = ("steady", "rising", "surge")
STATE = Path(__file__).resolve().parent / "state"  # buffer and fetched credentials (git-ignored)


def level_for(profile, step, warning, danger):
    """Synthetic gauge level (m) for step n of a profile."""
    if profile == "steady":
        return round(warning - 1.0 + 0.02 * (step % 5), 2)
    if profile == "rising":  # from 1 m below warning, +0.15 m per reading, up to 0.3 m above danger
        return round(min(warning - 1.0 + 0.15 * step, danger + 0.3), 2)
    if profile == "surge":  # a dam release or glacial-lake burst: jumps past danger on the third reading
        return round(warning - 0.5 if step < 2 else danger + 0.6, 2)
    raise ValueError(profile)


def reading(cls, value, ts, seq):
    field = {"gauge": "level_m", "tilt": "tilt_deg", "rain": "rain_24h_mm"}[cls]
    return {"ts": ts, field: value, "seq": seq}


class Buffer:
    """Readings that could not be sent, in a JSON-lines file, oldest first."""

    def __init__(self, path):
        self.path = Path(path)

    def add(self, item):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item) + "\n")

    def drain(self):
        if not self.path.exists():
            return []
        items = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.path.unlink()
        return items


class Station:
    def __init__(self, thing, zone, cls, client_factory, node_url=None, buffer=None, post=None):
        self.thing, self.zone, self.cls = thing, zone, cls
        self.topic = f"sm/dev/{zone}/{cls}"
        self.client_factory, self.client = client_factory, None
        self.node_url, self.buffer = node_url, buffer or Buffer(STATE / f"{thing}-buffer.jsonl")
        self.post = post or post_json

    def send(self, item):
        """Cloud first (buffering on failure), then the LAN node. Returns 'cloud' or 'buffered'."""
        if self.node_url:
            try:
                self.post(f"{self.node_url}/reading", {"zone_id": self.zone, "source_class": self.cls, "source_id": self.thing, **item})
            except OSError as exc:
                print(f"district node unreachable: {exc}")
        try:
            if self.client is None:
                self.client = self.client_factory().connect()
                for old in self.buffer.drain():  # store and forward, oldest first
                    self.client.publish(self.topic, json.dumps(old).encode())
            self.client.publish(self.topic, json.dumps(item).encode())
            return "cloud"
        except (OSError, mqtt.MqttError) as exc:
            print(f"cloud unreachable ({exc}); buffered")
            if self.client:
                self.client.close()
            self.client = None
            self.buffer.add(item)
            return "buffered"


def post_json(url, body, timeout=3):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL is the operator's LAN node
        return resp.status


def credentials(args):
    """Secret from Secrets Manager (default) or explicit files and flags."""
    if args.cert:
        return {"certificate_file": args.cert, "key_file": args.key, "endpoint": args.endpoint, "zone": args.zone,
                "class": args.cls, "warning_level_m": args.warning, "danger_level_m": args.danger}
    folder = STATE / args.thing
    cached = folder / "secret.json"
    if cached.exists():  # fetched before: the station can start with no cloud
        secret = json.loads(cached.read_text(encoding="utf-8"))
    else:
        import boto3  # only needed for the first fetch

        secret = json.loads(boto3.client("secretsmanager", region_name=args.region)
                            .get_secret_value(SecretId=f"sentinelmesh/devices/{args.thing}")["SecretString"])
        folder.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(secret), encoding="utf-8")
        os.chmod(cached, 0o600)
    (folder / "cert.pem").write_text(secret["certificate_pem"])
    (folder / "key.pem").write_text(secret["private_key_pem"])
    os.chmod(folder / "key.pem", 0o600)
    return {**secret, "certificate_file": str(folder / "cert.pem"), "key_file": str(folder / "key.pem")}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--thing", required=True)
    p.add_argument("--profile", choices=PROFILES, default="rising")
    p.add_argument("--every", type=float, default=20, help="seconds between readings")
    p.add_argument("--count", type=int, default=0, help="stop after this many readings (0 = run forever)")
    p.add_argument("--node", help="Local Alert Node URL on the LAN, e.g. http://192.168.43.10:8080")
    p.add_argument("--region", default="ap-southeast-2")
    p.add_argument("--cert")
    p.add_argument("--key")
    p.add_argument("--endpoint")
    p.add_argument("--zone")
    p.add_argument("--cls", default="gauge")
    p.add_argument("--warning", type=float, default=3.0)
    p.add_argument("--danger", type=float, default=4.5)
    args = p.parse_args(argv)

    creds = credentials(args)
    context = mqtt.tls_context(creds["certificate_file"], creds["key_file"])
    station = Station(args.thing, creds["zone"], creds["class"],
                      lambda: mqtt.Client(creds["endpoint"], args.thing, context), node_url=args.node)
    step = 0
    while not args.count or step < args.count:
        value = level_for(args.profile, step, float(creds["warning_level_m"]), float(creds["danger_level_m"]))
        item = reading(creds["class"], value, time.time(), step)
        print(f"{args.thing} #{step}: {value} -> {station.send(item)}")
        step += 1
        if not args.count or step < args.count:
            time.sleep(args.every)


if __name__ == "__main__":
    main()
