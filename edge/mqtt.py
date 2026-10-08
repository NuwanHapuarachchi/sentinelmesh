"""Minimal MQTT 3.1.1 client over mutual TLS, standard library only (no paho), for the edge programs.

Enough for a field station or district node talking to AWS IoT Core: CONNECT with the device certificate,
PUBLISH at QoS 0 or 1 (waiting for PUBACK), SUBSCRIBE at QoS 1 and read incoming PUBLISH packets,
PINGREQ and DISCONNECT. The device's client id must be its IoT thing name, because the IoT policy only
lets a certificate connect as its own thing.
"""

import socket
import ssl
import struct

CONNECT, CONNACK, PUBLISH, PUBACK, SUBSCRIBE, SUBACK, PINGREQ, PINGRESP, DISCONNECT = 1, 2, 3, 4, 8, 9, 12, 13, 14
CONNACK_ERRORS = {1: "unacceptable protocol version", 2: "client id rejected", 3: "server unavailable",
                  4: "bad user name or password", 5: "not authorised"}


class MqttError(Exception):
    pass


def encode_length(n):
    """MQTT 'remaining length': 7 bits per byte, high bit = more bytes follow."""
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def encode_string(s):
    raw = s.encode("utf-8")
    return struct.pack("!H", len(raw)) + raw


def packet(kind, flags, body):
    return bytes([(kind << 4) | flags]) + encode_length(len(body)) + body


def connect_packet(client_id, keepalive=60):
    variable = encode_string("MQTT") + bytes([4, 0x02]) + struct.pack("!H", keepalive)  # level 4, clean session
    return packet(CONNECT, 0, variable + encode_string(client_id))


def publish_packet(topic, payload, qos=1, packet_id=1):
    body = encode_string(topic) + (struct.pack("!H", packet_id) if qos else b"") + payload
    return packet(PUBLISH, qos << 1, body)


def subscribe_packet(topic, packet_id=1, qos=1):
    return packet(SUBSCRIBE, 0x02, struct.pack("!H", packet_id) + encode_string(topic) + bytes([qos]))


def read_packet(sock):
    """(type, flags, body) of the next packet."""
    first = _read_exact(sock, 1)[0]
    length, shift = 0, 0
    while True:
        byte = _read_exact(sock, 1)[0]
        length |= (byte & 0x7F) << shift
        if not byte & 0x80:
            break
        shift += 7
    return first >> 4, first & 0x0F, _read_exact(sock, length)


def parse_publish(flags, body):
    """(topic, payload, qos, packet_id) of an incoming PUBLISH."""
    (topic_len,) = struct.unpack("!H", body[:2])
    topic = body[2:2 + topic_len].decode("utf-8")
    rest, qos = body[2 + topic_len:], (flags >> 1) & 0x03
    packet_id = None
    if qos:
        (packet_id,), rest = struct.unpack("!H", rest[:2]), rest[2:]
    return topic, rest, qos, packet_id


def _read_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise MqttError("connection closed")
        data += chunk
    return data


def tls_context(cert_file, key_file, ca_file=None):
    """Client TLS with the device certificate; the server is checked against the system CA store (or ca_file)."""
    ctx = ssl.create_default_context(cafile=ca_file)
    ctx.load_cert_chain(cert_file, key_file)
    return ctx


class Client:
    def __init__(self, host, client_id, context, port=8883, keepalive=60, timeout=10, connector=None):
        self.host, self.port, self.client_id = host, port, client_id
        self.context, self.keepalive, self.timeout = context, keepalive, timeout
        self.connector = connector  # tests inject a fake socket factory
        self.sock, self._next_id = None, 0

    def connect(self):
        if self.connector:
            self.sock = self.connector()
        else:
            raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self.sock = self.context.wrap_socket(raw, server_hostname=self.host)
        self.sock.sendall(connect_packet(self.client_id, self.keepalive))
        kind, _, body = read_packet(self.sock)
        if kind != CONNACK or len(body) != 2:
            raise MqttError(f"expected CONNACK, got packet type {kind}")
        if body[1]:
            raise MqttError(f"connection refused: {CONNACK_ERRORS.get(body[1], body[1])}")
        return self

    def packet_id(self):
        self._next_id = self._next_id % 65535 + 1
        return self._next_id

    def publish(self, topic, payload, qos=1):
        pid = self.packet_id()
        self.sock.sendall(publish_packet(topic, payload, qos, pid))
        if qos:
            while True:  # skip anything else until our PUBACK
                kind, _, body = read_packet(self.sock)
                if kind == PUBACK and struct.unpack("!H", body[:2])[0] == pid:
                    return pid

    def subscribe(self, topic):
        pid = self.packet_id()
        self.sock.sendall(subscribe_packet(topic, pid))
        while True:
            kind, _, body = read_packet(self.sock)
            if kind == SUBACK and struct.unpack("!H", body[:2])[0] == pid:
                if body[2] == 0x80:
                    raise MqttError(f"subscription to {topic} refused")
                return pid

    def receive(self):
        """Block until the next incoming PUBLISH; returns (topic, payload). Acknowledges QoS 1."""
        while True:
            kind, flags, body = read_packet(self.sock)
            if kind == PUBLISH:
                topic, payload, qos, pid = parse_publish(flags, body)
                if qos == 1:
                    self.sock.sendall(packet(PUBACK, 0, struct.pack("!H", pid)))
                return topic, payload

    def ping(self):
        self.sock.sendall(packet(PINGREQ, 0, b""))

    def close(self):
        if self.sock:
            try:
                self.sock.sendall(packet(DISCONNECT, 0, b""))
            except OSError:
                pass
            self.sock.close()
            self.sock = None
