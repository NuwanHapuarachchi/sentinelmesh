import json

import pytest

from edge import field_gateway as fg
from edge import mqtt


def test_profiles():
    assert fg.level_for("rising", 0, 3.0, 4.5) == 2.0
    assert fg.level_for("rising", 100, 3.0, 4.5) == 4.8  # capped 0.3 m above danger
    assert [fg.level_for("surge", s, 3.0, 4.5) for s in range(4)] == [2.5, 2.5, 5.1, 5.1]
    assert max(fg.level_for("steady", s, 3.0, 4.5) for s in range(10)) < 3.0
    with pytest.raises(ValueError):
        fg.level_for("unknown", 0, 3.0, 4.5)


def test_reading_uses_the_class_field():
    assert fg.reading("gauge", 3.2, 100.0, 1) == {"ts": 100.0, "level_m": 3.2, "seq": 1}
    assert fg.reading("tilt", 0.8, 100.0, 2) == {"ts": 100.0, "tilt_deg": 0.8, "seq": 2}


class FakeClient:
    def __init__(self, broken=False):
        self.broken, self.published = broken, []

    def connect(self):
        if self.broken:
            raise OSError("network is unreachable")
        return self

    def publish(self, topic, payload, qos=1):
        self.published.append((topic, json.loads(payload)))

    def close(self):
        pass


def station(tmp_path, clients, posted):
    return fg.Station("sentinelmesh-gauge-Z07", "Z07", "gauge", lambda: clients.pop(0), node_url="http://node:8080",
                      buffer=fg.Buffer(tmp_path / "buffer.jsonl"), post=lambda url, body: posted.append((url, body)))


def test_publishes_to_its_own_topic_and_mirrors_to_the_district_node(tmp_path):
    cloud, posted = FakeClient(), []
    s = station(tmp_path, [cloud], posted)
    assert s.send({"ts": 1, "level_m": 3.2, "seq": 0}) == "cloud"
    assert cloud.published == [("sm/dev/Z07/gauge", {"ts": 1, "level_m": 3.2, "seq": 0})]
    assert posted == [("http://node:8080/reading", {"zone_id": "Z07", "source_class": "gauge",
                                                    "source_id": "sentinelmesh-gauge-Z07", "ts": 1, "level_m": 3.2, "seq": 0})]


def test_buffers_while_the_cloud_is_down_and_replays_oldest_first(tmp_path):
    later = FakeClient()
    s = station(tmp_path, [FakeClient(broken=True), FakeClient(broken=True), later], [])
    assert s.send({"seq": 0}) == "buffered"
    assert s.send({"seq": 1}) == "buffered"
    assert s.send({"seq": 2}) == "cloud"
    assert [p["seq"] for _, p in later.published] == [0, 1, 2]
    assert not (tmp_path / "buffer.jsonl").exists()


def test_an_unreachable_node_does_not_stop_the_cloud_send(tmp_path):
    cloud = FakeClient()

    def unreachable(url, body):
        raise OSError("no route to host")

    s = fg.Station("t", "Z07", "gauge", lambda: cloud, node_url="http://node:8080",
                   buffer=fg.Buffer(tmp_path / "b.jsonl"), post=unreachable)
    assert s.send({"seq": 0}) == "cloud" and len(cloud.published) == 1


def test_a_refused_certificate_buffers_like_an_outage(tmp_path):
    class Refused(FakeClient):
        def connect(self):
            raise mqtt.MqttError("connection refused: not authorised")

    s = station(tmp_path, [Refused()], [])
    assert s.send({"seq": 0}) == "buffered"


def test_credentials_are_fetched_once_then_work_offline(stack, tmp_path, monkeypatch):
    monkeypatch.setattr(fg, "STATE", tmp_path)
    secret = {"certificate_pem": "CERT", "private_key_pem": "KEY", "endpoint": "e", "zone": "Z07", "class": "gauge",
              "warning_level_m": 3.0, "danger_level_m": 4.5}
    stack.secrets.create_secret(Name="sentinelmesh/devices/sentinelmesh-gauge-Z07", SecretString=json.dumps(secret))
    args = fg.argparse.Namespace(cert=None, thing="sentinelmesh-gauge-Z07", region="ap-southeast-2")
    first = fg.credentials(args)
    stack.secrets.delete_secret(SecretId="sentinelmesh/devices/sentinelmesh-gauge-Z07", ForceDeleteWithoutRecovery=True)
    second = fg.credentials(args)  # the cloud copy is gone: the cached one is used
    assert first == second and second["zone"] == "Z07"
    assert (tmp_path / "sentinelmesh-gauge-Z07" / "key.pem").read_text() == "KEY"
