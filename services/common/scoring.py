"""Corroboration scoring: turns per-source evidence for one zone into a risk score and tier.

Pure functions only (no AWS calls) so every rule is unit-tested.

Key properties the tests lock in:
- One source class alone never goes above "watch".
- Citizen reports are capped (max contribution 0.25) and can never trigger "imminent".
- "imminent" needs agreement that includes an authoritative source (gauge, tilt, rain, forecast),
  or a fresh gauge reading at the zone's danger level (the same hard threshold as the IoT bypass).
- Silence from physical sensors marks the zone "blind"; blind + rising trend escalates (max warning).
- Tiers rise immediately but only fall after the score stays below an exit threshold for hold_down_s.
"""

from dataclasses import dataclass, field

TIERS = ["normal", "watch", "advisory", "warning", "imminent"]
RANK = {t: i for i, t in enumerate(TIERS)}

WEIGHTS = {"gauge": 0.40, "tilt": 0.40, "rain": 0.30, "forecast": 0.30, "report": 0.25}
AUTHORITATIVE = {"gauge", "tilt", "rain", "forecast"}
PHYSICAL = {"gauge", "tilt", "rain"}
HAZARD_CLASSES = {
    "flood": ("gauge", "rain", "forecast", "report"),
    "landslide": ("tilt", "rain", "forecast", "report"),
}
REPORT_KINDS = {"flood": {"flooding", "water_rising"}, "landslide": {"landslide", "crack"}}

AGREE = 0.5
# NBRO-style 24 h rainfall anchors (mm) and tilt anchors (degrees).
RAIN_ANCHORS = [(0, 0.0), (75, 0.5), (100, 0.7), (150, 1.0)]
TILT_ANCHORS = [(0, 0.0), (0.5, 0.5), (1.0, 0.7), (2.0, 1.0)]

# (fresh multiplier of cadence, half-life s, max age s)
DECAY = {
    "gauge": (2.0, 900, 3600),
    "tilt": (2.0, 900, 3600),
    "rain": (2.0, 1800, 7200),
    "forecast": (1.5, 10800, 21600),
    "report": (1.0, 1800, 7200),
}
CADENCE_DEFAULT = {"forecast": 3600, "report": 600}

ENTER = {"imminent": 0.80, "warning": 0.55, "advisory": 0.35, "watch": 0.20}
EXIT = {"imminent": 0.70, "warning": 0.45, "advisory": 0.25, "watch": 0.15}
RISING_M_PER_H = 0.1
SILENCE_FACTOR = 3


def interp(anchors, x):
    if x is None:
        return 0.0
    if x <= anchors[0][0]:
        return anchors[0][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:], strict=False):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return anchors[-1][1]


def freshness(cls, age_s, cadence_s):
    mult, half_life, max_age = DECAY[cls]
    fresh = mult * cadence_s
    if age_s <= fresh:
        return 1.0
    if age_s >= max_age:
        return 0.0
    return 0.5 ** ((age_s - fresh) / half_life)


def gauge_slope_m_per_h(history):
    """Least-squares slope over [(ts, level_m), ...]."""
    if len(history) < 2:
        return 0.0
    n = len(history)
    ts = [t for t, _ in history]
    ys = [y for _, y in history]
    mt, my = sum(ts) / n, sum(ys) / n
    den = sum((t - mt) ** 2 for t in ts)
    if den == 0:
        return 0.0
    return sum((t - mt) * (y - my) for t, y in zip(ts, ys, strict=True)) / den * 3600


def severity(cls, value, zone, gauge_history=()):
    if cls == "gauge":
        anchors = [(0, 0.0), (zone["warning_level_m"], 0.6), (zone["danger_level_m"], 1.0)]
        s = interp(anchors, value)
        if gauge_slope_m_per_h(gauge_history) >= zone.get("rise_fast_m_per_h", 0.3):
            s = min(1.0, s + 0.1)
        return s
    if cls in ("rain", "forecast"):
        return interp(RAIN_ANCHORS, value)
    if cls == "tilt":
        return interp(TILT_ANCHORS, value)
    raise ValueError(cls)


def report_severity(reports, hazard, now):
    """Distinct reporters' max severity (1-3), decayed; three severe reporters saturate at 1.0."""
    best = {}
    for r in reports:
        if r["kind"] not in REPORT_KINDS[hazard]:
            continue
        f = freshness("report", now - r["ts"], CADENCE_DEFAULT["report"])
        if f <= 0:
            continue
        best[r["h"]] = max(best.get(r["h"], 0.0), (r["sev"] / 3) * f)
    return min(1.0, sum(best.values()) / 3)


@dataclass
class Assessment:
    score: float = 0.0
    tier: str = "normal"
    hazard: str = "flood"
    contributions: dict = field(default_factory=dict)
    agreeing: list = field(default_factory=list)
    blind: bool = False
    reason: str = "score"


def assess_hazard(zone, hazard, evidence, reports, gauge_history, now):
    cadence = zone.get("gauge_cadence_s", 60)
    eff = {}
    for cls in HAZARD_CLASSES[hazard]:
        if cls == "report":
            eff[cls] = report_severity(reports, hazard, now)
            continue
        ev = evidence.get(cls)
        if not ev:
            continue
        s = severity(cls, ev["value"], zone, gauge_history)
        eff[cls] = s * freshness(cls, now - ev["ts"], CADENCE_DEFAULT.get(cls, cadence))

    contributions = {cls: round(WEIGHTS[cls] * s, 4) for cls, s in eff.items() if s > 0}
    score = min(1.0, sum(contributions.values()))
    agreeing = sorted(cls for cls, s in eff.items() if s >= AGREE)

    gauge = evidence.get("gauge")
    hard = (
        hazard == "flood"
        and gauge is not None
        and gauge["value"] >= zone["danger_level_m"]
        and now - gauge["ts"] <= DECAY["gauge"][0] * cadence
    )
    if hard:
        tier, reason = "imminent", "hard_threshold"
    elif score >= ENTER["imminent"] and len(agreeing) >= 2 and AUTHORITATIVE & set(agreeing):
        tier, reason = "imminent", "corroborated"
    elif score >= ENTER["warning"] and len(agreeing) >= 2:
        tier, reason = "warning", "corroborated"
    elif score >= ENTER["advisory"] and len(agreeing) >= 2:
        tier, reason = "advisory", "corroborated"
    elif agreeing or score >= ENTER["watch"]:
        tier, reason = "watch", "single_source" if len(agreeing) <= 1 else "corroborated"
    else:
        tier, reason = "normal", "score"
    return Assessment(round(score, 4), tier, hazard, contributions, agreeing, False, reason)


def is_blind(evidence, last_seen, cadence_s, now):
    """True when every physical class that has ever reported has gone quiet for SILENCE_FACTOR x cadence."""
    seen = {c: ts for c, ts in last_seen.items() if c in PHYSICAL}
    if not seen:
        return False
    return all(now - ts > SILENCE_FACTOR * cadence_s for ts in seen.values())


def assess(zone, evidence, reports, gauge_history, last_seen, previous, now):
    """Score every hazard, take the worst, then apply blind escalation.

    previous: the zone's last stored state ({"tier", "score_hist": [(ts, score)], ...}) or {}.
    """
    hazards = ("flood", "landslide")
    results = [assess_hazard(zone, h, evidence, reports, gauge_history, now) for h in hazards]
    best = max(results, key=lambda a: (RANK[a.tier], a.score))

    cadence = zone.get("gauge_cadence_s", 60)
    best.blind = is_blind(evidence, last_seen, cadence, now)
    if best.blind:
        rising = gauge_slope_m_per_h(gauge_history) >= RISING_M_PER_H or _score_rising(previous, now)
        prev_tier = previous.get("tier", "normal")
        if rising and RANK[prev_tier] >= RANK["watch"] and RANK[best.tier] < RANK["warning"]:
            best.tier, best.reason = "warning", "blind_escalation"
    return best


def _score_rising(previous, now):
    hist = [(t, s) for t, s in previous.get("score_hist", []) if now - t <= 900]
    return len(hist) >= 2 and hist[-1][1] - hist[0][1] >= 0.1


def next_tier(current, candidate, score, agreeing, pending_down_since, now, hold_down_s):
    """Hysteresis. Returns (tier, pending_down_since)."""
    if RANK[candidate] >= RANK[current]:
        return candidate, None
    # Candidate is lower: only step down once the score has stayed below the current tier's exit threshold.
    below = score < EXIT[current] if current != "watch" else (not agreeing and score < EXIT["watch"])
    if not below:
        return current, None
    if pending_down_since is None:
        return current, now
    if now - pending_down_since >= hold_down_s:
        return candidate, None
    return current, pending_down_since


def execution_name(zone_id, tier, episode, drill=False):
    """Deterministic Step Functions execution name: retries and duplicate triggers collapse into one."""
    name = f"{zone_id}-{tier}-e{episode}" + ("-d" if drill else "")
    return name[:80]
