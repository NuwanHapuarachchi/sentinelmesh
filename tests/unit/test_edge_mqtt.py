import socket
import threading

import pytest

from edge import mqtt


@pytest.mark.parametrize(("n", "encoded"), [(0, "00"), (127, "7f"), (128, "8001"), (16383, "ff7f"), (16384, "808001"), (2097151, "ffff7f")])
def test_remaining_length_matches_the_spec_examples(n, encoded):
    assert mqtt.encode_length(n).hex() == encoded


def test_connect_packet_bytes():
    # fixed header, "MQTT", level 4, clean session, keepalive 60, client id "abc"
    assert mqtt.connect_packet("abc", 60).hex() == "100f00044d5154540402003c0003616263"


def test_publish_and_subscribe_packet_bytes():
    assert mqtt.publish_packet("a/b", b"x", qos=1, packet_id=1).hex() == "3208" + "0003612f62" + "0001" + "78"
    assert mqtt.publish_packet("a/b", b"x", qos=0).hex() == "3006" + "0003612f62" + "78"
    assert mqtt.subscribe_packet("a/b", packet_id=1).hex() == "8208" + "0001" + "0003612f62" + "01"


def test_large_payload_uses_a_multi_byte_length():
    raw = mqtt.publish_packet("t", b"x" * 300, qos=0)
    kind_flags, length = raw[0], raw[1:3]
    assert kind_flags == 0x30 and mqtt.encode_length(2 + 1 + 300) == length


class Broker:
    """A scripted broker on one end of a socket pair: replies to each packet type as told and records them."""

    def __init__(self, connack_rc=0):
        self.client_sock, self.sock = socket.socketpair()
        self.received, self.connack_rc = [], connack_rc
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            while True:
                kind, flags, body = mqtt.read_packet(self.sock)
                self.received.append((kind, flags, body))
                if kind == mqtt.CONNECT:
                    self.sock.sendall(mqtt.packet(mqtt.CONNACK, 0, bytes([0, self.connack_rc])))
                elif kind == mqtt.PUBLISH and (flags >> 1) & 3:
                    _, _, _, pid = mqtt.parse_publish(flags, body)
                    self.sock.sendall(mqtt.packet(mqtt.PUBACK, 0, pid.to_bytes(2, "big")))
                elif kind == mqtt.SUBSCRIBE:
                    pid = body[:2]
                    self.sock.sendall(mqtt.packet(mqtt.SUBACK, 0, pid + b"\x01"))
                    self.sock.sendall(mqtt.publish_packet("sm/alerts/Kegalle", b'{"alert_id":"A1"}', qos=1, packet_id=77))
                elif kind == mqtt.DISCONNECT:
                    return
        except mqtt.MqttError:
            return


def test_connect_publish_subscribe_receive_and_close():
    broker = Broker()
    client = mqtt.Client("example", "sentinelmesh-gauge-Z07", context=None, connector=lambda: broker.client_sock).connect()
    assert client.publish("sm/dev/Z07/gauge", b'{"level_m": 3.2}') == 1
    client.subscribe("sm/alerts/Kegalle")
    assert client.receive() == ("sm/alerts/Kegalle", b'{"alert_id":"A1"}')
    client.close()
    broker.thread.join(timeout=2)
    kinds = [k for k, _, _ in broker.received]
    assert kinds == [mqtt.CONNECT, mqtt.PUBLISH, mqtt.SUBSCRIBE, mqtt.PUBACK, mqtt.DISCONNECT]
    puback = broker.received[3][2]
    assert puback == (77).to_bytes(2, "big"), "the client acknowledged the broker's QoS 1 message"


def test_refused_connection_is_an_error():
    broker = Broker(connack_rc=5)
    with pytest.raises(mqtt.MqttError, match="not authorised"):
        mqtt.Client("example", "revoked-thing", context=None, connector=lambda: broker.client_sock).connect()
