"""Generate the hazard-zone catalogue used by Terraform, the services, the tests and the web map.

80 demo zones are hexagons laid over six districts of the Kelani/Kalu basins, plus isolated
test zones (T01-T04 for e2e tests; H00, H01, H02 for the per-cell heartbeat, one in each cell including
the standby). Output is deterministic.

    python scripts/gen_zones.py
"""

import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZONES_JSON = ROOT / "terraform" / "app" / "data" / "zones.json"
GEOJSON = ROOT / "web" / "zones.geojson"

HEX_RADIUS_DEG = 0.022  # ~2.4 km

# (district, centre lat, centre lon, zone count, dominant hazard)
DISTRICTS = [
    ("Ratnapura", 6.70, 80.40, 18, "flood"),
    ("Kalutara", 6.60, 80.15, 14, "flood"),
    ("Kegalle", 7.25, 80.35, 14, "landslide"),
    ("Colombo", 6.93, 80.08, 10, "flood"),
    ("Gampaha", 7.08, 80.02, 12, "flood"),
    ("Nuwara Eliya", 6.97, 80.70, 12, "landslide"),
]

# Test zones sit far from the demo zones and use short timers so e2e tests finish quickly.
TEST_ZONES = [
    ("T01", "a"),
    ("T02", "a"),
    ("T03", "b"),
    ("T04", "b"),
    ("H00", "a"),  # one heartbeat zone per cell, so every cell's full path is proven every few minutes
    ("H01", "b"),
    ("H02", "c"),  # c is the warm standby: its heartbeat is the evidence that it really is warm
]
TEST_CENTRE = (6.45, 81.05)
LOAD_CENTRE = (6.20, 81.20)


def spiral(count):
    """Axial hex coordinates in rings around the centre."""
    coords = [(0, 0)]
    directions = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]
    ring = 1
    while len(coords) < count:
        q, r = -ring, ring  # start at the "south-west" corner of the ring
        for dq, dr in directions:
            for _ in range(ring):
                coords.append((q, r))
                q, r = q + dq, r + dr
        ring += 1
    return coords[:count]


def hexagon(lat, lon, radius):
    """Closed pointy-top hexagon as [lon, lat] pairs."""
    scale = math.cos(math.radians(lat))
    points = []
    for k in range(6):
        angle = math.radians(30 + 60 * k)
        points.append([round(lon + radius * math.cos(angle) / scale, 5), round(lat + radius * math.sin(angle), 5)])
    points.append(points[0])
    return points


def centre_of(lat0, lon0, q, r, radius):
    scale = math.cos(math.radians(lat0))
    x = radius * math.sqrt(3) * (q + r / 2)
    y = radius * 1.5 * r
    return round(lat0 + y, 5), round(lon0 + x / scale, 5)


def jitter(zone_id, low, high):
    """Deterministic value in [low, high] derived from the zone id."""
    h = int(hashlib.sha256(zone_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return round(low + (high - low) * h, 1)


def zone_record(zone_id, name, district, hazard, lat, lon, owner_cell, is_test):
    warning = jitter(zone_id, 3.0, 4.0)
    return {
        "zone_id": zone_id,
        "name": name,
        "district": district,
        "hazard": hazard,
        "lat": lat,
        "lon": lon,
        "polygon": hexagon(lat, lon, HEX_RADIUS_DEG),
        "owner_cell": owner_cell,
        "warning_level_m": warning,
        "danger_level_m": round(warning + jitter(zone_id + "d", 1.0, 1.5), 1),
        "gauge_cadence_s": 20 if is_test else 60,
        # T01 keeps a longer approval window so the e2e "officer approves" test is not racing the timeout.
        "approval_timeout_s": (60 if zone_id == "T01" else 20) if is_test else 300,
        "is_test": is_test,
    }


def build():
    zones = []
    number = 1
    for district, lat0, lon0, count, hazard in DISTRICTS:
        for i, (q, r) in enumerate(spiral(count)):
            zone_id = f"Z{number:02d}"
            lat, lon = centre_of(lat0, lon0, q, r, HEX_RADIUS_DEG)
            # Mix hazards a little so both flood and landslide logic appear in every district.
            zone_hazard = hazard if i % 4 else ("landslide" if hazard == "flood" else "flood")
            owner = "a" if number <= 40 else "b"
            zones.append(zone_record(zone_id, f"{district} {i + 1:02d}", district, zone_hazard, lat, lon, owner, False))
            number += 1
    for i, (zone_id, owner) in enumerate(TEST_ZONES):
        lat, lon = centre_of(*TEST_CENTRE, i, 0, HEX_RADIUS_DEG)
        zones.append(zone_record(zone_id, f"Test {zone_id}", "Test", "flood", lat, lon, owner, True))
    # Load-test zones (tests/load/burst.py): hidden, split across the two cells like the real ones.
    for i in range(1, 81):
        lat, lon = centre_of(*LOAD_CENTRE, i % 10, i // 10, HEX_RADIUS_DEG)
        zones.append(zone_record(f"L{i:02d}", f"Load {i:02d}", "Load", "flood", lat, lon, "a" if i <= 40 else "b", True))
    return zones


def main():
    zones = build()
    ZONES_JSON.parent.mkdir(parents=True, exist_ok=True)
    ZONES_JSON.write_text(json.dumps(zones, indent=1) + "\n", encoding="utf-8")

    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [z["polygon"]]},
            "properties": {k: z[k] for k in ("zone_id", "name", "district", "hazard", "owner_cell", "is_test")},
        }
        for z in zones
    ]
    GEOJSON.parent.mkdir(parents=True, exist_ok=True)
    GEOJSON.write_text(json.dumps({"type": "FeatureCollection", "features": features}) + "\n", encoding="utf-8")
    print(f"wrote {len(zones)} zones -> {ZONES_JSON.relative_to(ROOT)}, {GEOJSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
