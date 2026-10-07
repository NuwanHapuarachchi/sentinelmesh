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

import boto3

from common.observe import log, metric

ddb = boto3.resource("dynamodb")
iot = boto3.client("iot-data", endpoint_url=f"https://{os.environ['IOT_ENDPOINT']}")
zones = ddb.Table(os.environ["ZONES_TABLE"])
URL = "https://api.open-meteo.com/v1/forecast"


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
    for name, result in zip(names, results, strict=True):
        next_24h = round(sum((result["hourly"]["precipitation"] or [0])[:24] or [0]), 1)
        metric("ForecastRain24hMm", next_24h, "None", District=name)
        for z in districts[name]:
            iot.publish(topic=f"sm/obs/{z['zone_id']}", qos=0, payload=json.dumps({
                "source_class": "forecast", "source_id": "open-meteo", "ts": time.time(), "fc_rain_24h_mm": next_24h,
            }))
            published += 1
    log("forecast_published", districts=len(names), observations=published)
    return {"observations": published}
