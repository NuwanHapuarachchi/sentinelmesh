"""Forecast feed: hourly, real rainfall forecasts from Open-Meteo (free, no key) for each district
centroid. The next-24-hour total becomes a "forecast" observation for every zone in that district,
an independent source that keeps zones scored even when their gauges are down.

Environment: ZONES_TABLE, IOT_ENDPOINT.
"""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta, timezone

import boto3

from common.observe import log, metric

ddb = boto3.resource("dynamodb")
iot = boto3.client("iot-data", endpoint_url=f"https://{os.environ['IOT_ENDPOINT']}")
zones = ddb.Table(os.environ["ZONES_TABLE"])
URL = "https://api.open-meteo.com/v1/forecast"
SLT = timezone(timedelta(hours=5, minutes=30))  # the request asks for Asia/Colombo local times


def next_24h_mm(hourly, now):
    """Rain over the 24 hours starting at the current local hour. The hourly arrays start at local midnight
    today, so the first 24 values would be the calendar day (mostly past rain late in the evening). Missing
    hours (null) count as no rain."""
    current = datetime.fromtimestamp(now, UTC).astimezone(SLT).strftime("%Y-%m-%dT%H:00")
    times = hourly.get("time") or []
    start = times.index(current) if current in times else 0
    return round(sum(p or 0 for p in (hourly.get("precipitation") or [])[start:start + 24]), 1)


def handler(event, context):
    items = [z for z in zones.scan()["Items"] if not z.get("is_test")]
    districts = {}
    for z in items:
        districts.setdefault(z["district"], []).append(z)
    names = sorted(districts)
    lats = [sum(float(z["lat"]) for z in districts[d]) / len(districts[d]) for d in names]
    lons = [sum(float(z["lon"]) for z in districts[d]) / len(districts[d]) for d in names]
    query = urllib.parse.urlencode({
        "latitude": ",".join(f"{v:.3f}" for v in lats), "longitude": ",".join(f"{v:.3f}" for v in lons),
        "hourly": "precipitation", "forecast_days": 2, "timezone": "Asia/Colombo",
    })
    with urllib.request.urlopen(f"{URL}?{query}", timeout=10) as resp:  # noqa: S310 - fixed public URL
        data = json.loads(resp.read())
    results = data if isinstance(data, list) else [data]

    published = 0
    now = time.time()
    for name, result in zip(names, results, strict=True):
        next_24h = next_24h_mm(result["hourly"], now)
        metric("ForecastRain24hMm", next_24h, "None", District=name)
        for z in districts[name]:
            iot.publish(topic=f"sm/obs/{z['zone_id']}", qos=0, payload=json.dumps({
                "source_class": "forecast", "source_id": "open-meteo", "ts": time.time(), "fc_rain_24h_mm": next_24h,
            }))
            published += 1
    log("forecast_published", districts=len(names), observations=published)
    return {"observations": published}
