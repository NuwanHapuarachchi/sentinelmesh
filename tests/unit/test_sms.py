import json
import urllib.parse

import pytest

from common import sms

SECRET = {"api_key": "k123", "sender_id": "SentinelMsh"}
BASE = {"SMS_HTTP_URL": "https://gw.example/send"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0771234567", "+94771234567"), ("+94 77 123 4567", "+94771234567"), ("771234567", "+94771234567")],
)
def test_normalise_sri_lankan_numbers(raw, expected):
    assert sms.normalise_msisdn(raw) == expected


def test_json_bearer_request():
    url, headers, body = sms.build_request("0771234567", "hello", "A1|S1", SECRET, BASE)
    assert url == BASE["SMS_HTTP_URL"]
    assert headers["Authorization"] == "Bearer k123"
    data = json.loads(body)
    assert data == {"to": "+94771234567", "message": "hello", "ref": "A1|S1", "sender_id": "SentinelMsh"}


def test_form_with_query_key_and_unicode_flag():
    env = {**BASE, "SMS_HTTP_FORMAT": "form", "SMS_HTTP_AUTH": "query", "SMS_HTTP_UNICODE_FIELD": "unicode"}
    url, headers, body = sms.build_request("0771234567", "හදිසි", "R", SECRET, env)
    assert "api_key=k123" in url
    fields = urllib.parse.parse_qs(body.decode())
    assert fields["unicode"] == ["1"] and fields["message"] == ["හදිසි"]


def test_unicode_flag_not_set_for_english():
    env = {**BASE, "SMS_HTTP_UNICODE_FIELD": "unicode"}
    _, _, body = sms.build_request("0771234567", "hello", "R", SECRET, env)
    assert "unicode" not in json.loads(body)


def test_key_in_body_or_header():
    _, headers, _ = sms.build_request("1", "x", "R", SECRET, {**BASE, "SMS_HTTP_AUTH": "header", "SMS_HTTP_AUTH_FIELD": "X-Key"})
    assert headers["X-Key"] == "k123"
    _, _, body = sms.build_request("1", "x", "R", SECRET, {**BASE, "SMS_HTTP_AUTH": "body"})
    assert json.loads(body)["api_key"] == "k123"


def test_extract_provider_id():
    assert sms.extract_id('{"data": {"messages": [{"id": 42}]}}', "data.messages.0.id") == "42"
    assert sms.extract_id("not json", "id") is None


@pytest.mark.parametrize(
    ("params", "expected"),
    [({"ref": "A|S", "status": "DELIVERED"}, ("A|S", "DELIVERED")), ({"reference": "X", "state": "undeliv"}, ("X", "FAILED"))],
)
def test_parse_delivery_reports(params, expected):
    assert sms.parse_dlr(params) == expected


def test_dlr_secret_check():
    assert sms.dlr_secret_ok("s3cret", "s3cret")
    assert not sms.dlr_secret_ok("wrong", "s3cret")
    assert not sms.dlr_secret_ok("", "")
