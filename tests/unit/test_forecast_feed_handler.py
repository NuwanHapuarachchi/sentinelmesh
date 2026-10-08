import io
import json
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

SLT = timezone(timedelta(hours=5, minutes=30))
MIDNIGHT = datetime(2026, 10, 8, tzinfo=SLT)


def hourly(values):
    """Open-Meteo hourly block: 48 hours from local midnight today."""
    times = [(MIDNIGHT + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(48)]
    return {"time": times, "precipitation": values}


@pytest.fixture
def feed(stack):
    return stack.load("forecast_feed.handler")


def test_window_starts_at_the_current_local_hour(feed):
    # 1 mm every hour today, 5 mm every hour tomorrow. At 22:30 the next 24 h are 2 h of today + 22 h of tomorrow.
    values = [1.0] * 24 + [5.0] * 24
    now = (MIDNIGHT + timedelta(hours=22, minutes=30)).timestamp()
    assert feed.next_24h_mm(hourly(values), now) == 2 * 1.0 + 22 * 5.0


def test_missing_hours_count_as_no_rain(feed):
    values = [None, 2.0, None] + [1.0] * 45
    assert feed.next_24h_mm(hourly(values), MIDNIGHT.timestamp()) == 2.0 + 21 * 1.0


def test_unknown_current_hour_falls_back_to_the_start(feed):
    later = (MIDNIGHT + timedelta(days=5)).timestamp()
    assert feed.next_24h_mm(hourly([1.0] * 48), later) == 24.0


def test_handler_publishes_one_forecast_per_real_zone(stack, feed, monkeypatch):
    district_count = 6
    body = json.dumps([{"hourly": hourly([0.5] * 48)} for _ in range(district_count)]).encode()

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    requested = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: requested.append(url) or Reply(body))
    published = []
    feed.iot.publish = lambda **kw: published.append(kw)

    out = feed.handler({}, None)
    assert out == {"observations": 80}
    assert "timezone=Asia%2FColombo" in requested[0]
    payloads = [json.loads(p["payload"]) for p in published]
    assert {p["source_class"] for p in payloads} == {"forecast"}
    assert {p["fc_rain_24h_mm"] for p in payloads} == {12.0}
    assert not [p for p in published if not p["topic"].startswith("sm/obs/Z")]
