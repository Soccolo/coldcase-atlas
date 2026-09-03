"""
Turning raw police.uk records into a ranked case portfolio.

This is the analytical core. Everything here runs on the official open data
only - there is no personal data in any of it, which is what keeps the whole
app on the right side of UK GDPR Art. 10 (see README).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from .sources.police_uk import Crime

# --------------------------------------------------------------------------
# Case status
# --------------------------------------------------------------------------
# police.uk publishes a free-text outcome category. These are the real values
# observed in the live feed; mapping them is what separates a genuinely cold
# case (no suspect identified) from an administratively stalled one
# (unable to prosecute - they know who, they cannot charge).

COLD = "cold"            # no suspect was ever identified
STALLED = "stalled"      # suspect known, prosecution failed or was dropped
OPEN = "open"            # still live, outcome pending
RESOLVED = "resolved"    # charged, cautioned, sentenced or otherwise disposed
UNKNOWN = "unknown"      # force did not supply an outcome

_STATUS_MAP = {
    "Investigation complete; no suspect identified": COLD,
    "Unable to prosecute suspect": STALLED,
    "Formal action is not in the public interest": STALLED,
    "Further investigation is not in the public interest": STALLED,
    "Further action is not in the public interest": STALLED,
    "Awaiting court outcome": OPEN,
    "Under investigation": OPEN,
    "Defendant sent to Crown Court": OPEN,
    "Suspect charged as part of another case": RESOLVED,
    "Offender given a caution": RESOLVED,
    "Offender given a drugs possession warning": RESOLVED,
    "Offender given penalty notice": RESOLVED,
    "Offender given community sentence": RESOLVED,
    "Offender given suspended prison sentence": RESOLVED,
    "Offender sent to prison": RESOLVED,
    "Offender fined": RESOLVED,
    "Offender otherwise dealt with": RESOLVED,
    "Local resolution": RESOLVED,
    "Defendant found not guilty": RESOLVED,
    "Court result unavailable": UNKNOWN,
    "Status update unavailable": UNKNOWN,
    "Action to be taken by another organisation": UNKNOWN,
}

STATUS_LABEL = {
    COLD: "Cold - no suspect identified",
    STALLED: "Stalled - suspect known, not prosecuted",
    OPEN: "Open - outcome pending",
    RESOLVED: "Resolved",
    UNKNOWN: "No outcome published",
}

# How serious, roughly, on a 0-1 scale. Used only to rank what a human should
# look at first - it is not a claim about any individual case.
SEVERITY = {
    "violent-crime": 1.00,
    "robbery": 0.85,
    "possession-of-weapons": 0.75,
    "burglary": 0.65,
    "criminal-damage-arson": 0.55,
    "theft-from-the-person": 0.50,
    "vehicle-crime": 0.40,
    "drugs": 0.40,
    "other-theft": 0.35,
    "public-order": 0.35,
    "bicycle-theft": 0.20,
    "shoplifting": 0.20,
    "anti-social-behaviour": 0.15,
    "other-crime": 0.30,
}


def months_between(a: str, b: str) -> int:
    """Whole months from YYYY-MM `a` to YYYY-MM `b`."""
    try:
        ay, am = (int(x) for x in a.split("-")[:2])
        by, bm = (int(x) for x in b.split("-")[:2])
    except (ValueError, AttributeError):
        return 0
    return (by - ay) * 12 + (bm - am)


def status_of(crime: Crime, latest_month: str) -> str:
    """Classify one crime. An old record with no outcome is cold in practice."""
    if crime.outcome is None:
        # Forces run about two months behind before an outcome appears.
        # Beyond a year of silence, treat it as cold rather than pending.
        return OPEN if months_between(crime.month, latest_month) <= 12 else COLD
    return _STATUS_MAP.get(crime.outcome, UNKNOWN)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

_STATUS_WEIGHT = {COLD: 1.0, STALLED: 0.7, UNKNOWN: 0.5, OPEN: 0.25, RESOLVED: 0.0}


def coldness(crime: Crime, latest_month: str) -> float:
    """
    0-100: how much does this single record deserve a second look?

    Severity x unsolvedness x age, and nothing else. Location effects are
    deliberately kept out - they are reported separately as a hotspot excess,
    because multiplying them in here saturated the scale and made every
    serious recent case rank identically at 100.
    """
    w = _STATUS_WEIGHT[status_of(crime, latest_month)]
    if w == 0.0:
        return 0.0
    sev = SEVERITY.get(crime.category, 0.3)
    age = months_between(crime.month, latest_month)
    # Rises fast over the first two years then flattens: a case cold for ten
    # years is not five times more interesting than one cold for two.
    age_factor = 1 - math.exp(-age / 18) if age > 0 else 0.0
    return round(100 * sev * w * (0.35 + 0.65 * age_factor), 1)


def explain(crime: Crime, latest_month: str, hotspot_z: float = 0.0) -> dict:
    """
    Decompose a case's priority into the terms that produced it.

    The score is a judgement call dressed as a number, so it should be able to
    show its working rather than asking to be trusted.
    """
    st = status_of(crime, latest_month)
    w = _STATUS_WEIGHT[st]
    sev = SEVERITY.get(crime.category, 0.3)
    age = months_between(crime.month, latest_month)
    age_factor = 1 - math.exp(-age / 18) if age > 0 else 0.0
    age_term = 0.35 + 0.65 * age_factor
    base = round(100 * sev * w * age_term, 1)
    place_term = 100 * min(hotspot_z, 6.0) / 6.0

    return {
        "terms": [
            {"label": "Offence severity", "weight": sev,
             "note": f"{crime.category.replace('-', ' ')} on a 0-1 scale set in "
                     f"triage.SEVERITY"},
            {"label": "Unsolved status", "weight": w,
             "note": f"{STATUS_LABEL[st]} - resolved cases score 0 and drop out"},
            {"label": "Age", "weight": round(age_term, 3),
             "note": f"{age} months old; rises steeply for two years then flattens"},
        ],
        "case_score": base,
        "place_score": round(place_term, 1),
        "hotspot_z": round(hotspot_z, 1),
        "priority": round(0.75 * base + 0.25 * place_term, 1) if base else 0.0,
        "formula": "priority = 0.75 x (severity x status x age) + 0.25 x local excess",
    }


# --------------------------------------------------------------------------
# Hotspot detection
# --------------------------------------------------------------------------
# A first attempt at this clustered "N unsolved crimes of one category within
# R metres". In a quiet suburb that finds something. In Liverpool city centre
# it returned a "cluster" of 1040 crimes in 443 m, which is not a pattern -
# it is a nightlife district. Raw density is mostly a proxy for footfall.
#
# What matters is EXCESS: is this spot worse than the area around it? Each
# grid cell is therefore compared against a baseline built from its own
# surrounding ring and scored as a Poisson surprise. That is a simplified
# scan statistic, and it is scale-free - it behaves the same in a village
# and in a city centre.

def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _to_metres(lat: float, lng: float, lat0: float, lng0: float) -> tuple[float, float]:
    """Local equirectangular projection - accurate enough over a few km."""
    x = math.radians(lng - lng0) * 6371000.0 * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * 6371000.0
    return x, y


@dataclass
class Hotspot:
    """A cell carrying materially more unsolved crime than its surroundings."""
    category: str
    centre: tuple[float, float]
    cell_m: int
    observed: int
    expected: float
    excess_z: float
    ratio: float
    months: tuple[str, str]
    streets: list[str]
    crime_ids: list[str]

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "centre": {"lat": self.centre[0], "lng": self.centre[1]},
            "cell_m": self.cell_m,
            "observed": self.observed,
            "expected": round(self.expected, 1),
            "excess_z": round(self.excess_z, 1),
            "ratio": round(self.ratio, 1),
            "from_month": self.months[0],
            "to_month": self.months[1],
            "streets": self.streets[:8],
            "crime_ids": self.crime_ids[:50],
        }


def find_hotspots(crimes: list[Crime], latest_month: str, *,
                  cell_m: int = 200, ring_m: int = 1000,
                  min_observed: int = 6, min_z: float = 3.0) -> list[Hotspot]:
    """
    Grid the unsolved crimes, then score each cell against a baseline drawn
    from the ring of cells around it.

    A cell is reported only if it is busy in absolute terms (>= min_observed)
    AND statistically surprising against its own neighbourhood (z >= min_z).
    That second test is what stops an entire city centre lighting up.

    The caveat that survives all of this: police.uk snaps every record to an
    anonymised map point, and those points sit on street segments rather than
    at real coordinates. A cell can be busy because a snap point sits inside
    it. Treat a hotspot as a question, never as a finding.
    """
    live = [c for c in crimes
            if c.lat is not None
            and status_of(c, latest_month) in (COLD, STALLED, UNKNOWN)]
    if len(live) < min_observed:
        return []

    lat0 = sum(c.lat for c in live) / len(live)
    lng0 = sum(c.lng for c in live) / len(live)

    by_cat: dict[str, list[Crime]] = defaultdict(list)
    for c in live:
        by_cat[c.category].append(c)

    hotspots: list[Hotspot] = []
    for cat, items in by_cat.items():
        if len(items) < min_observed * 2:
            continue

        cells: dict[tuple[int, int], list[Crime]] = defaultdict(list)
        for c in items:
            x, y = _to_metres(c.lat, c.lng, lat0, lng0)
            cells[(int(x // cell_m), int(y // cell_m))].append(c)

        reach = max(1, int(ring_m // cell_m))
        for (cx, cy), group in cells.items():
            if len(group) < min_observed:
                continue

            # Baseline: mean per-cell count across the surrounding ring,
            # excluding the candidate cell so it cannot inflate its own
            # expectation. Empty cells count as zero - that is the point.
            ring_total, ring_cells = 0, 0
            for dx in range(-reach, reach + 1):
                for dy in range(-reach, reach + 1):
                    if dx == 0 and dy == 0:
                        continue
                    ring_total += len(cells.get((cx + dx, cy + dy), ()))
                    ring_cells += 1
            if ring_cells == 0:
                continue

            expected = max(ring_total / ring_cells, 0.5)   # floor avoids blowups
            observed = len(group)
            z = (observed - expected) / math.sqrt(expected)
            if z < min_z:
                continue

            lats = [c.lat for c in group]
            lngs = [c.lng for c in group]
            ms = sorted(c.month for c in group)
            hotspots.append(Hotspot(
                category=cat,
                centre=(sum(lats) / len(lats), sum(lngs) / len(lngs)),
                cell_m=cell_m,
                observed=observed,
                expected=expected,
                excess_z=z,
                ratio=observed / expected,
                months=(ms[0], ms[-1]),
                streets=sorted({c.street for c in group}),
                crime_ids=[c.persistent_id for c in group if c.persistent_id],
            ))

    hotspots.sort(key=lambda h: -h.excess_z)
    return hotspots


# --------------------------------------------------------------------------
# Portfolio view
# --------------------------------------------------------------------------

def build_portfolio(crimes: list[Crime], latest_month: str) -> dict:
    """Everything the UI needs, in one pass."""
    hotspots = find_hotspots(crimes, latest_month)

    # Map each crime to the strongest hotspot it belongs to.
    z_by_crime: dict[str, float] = {}
    for h in hotspots:
        for pid in h.crime_ids:
            z_by_crime[pid] = max(z_by_crime.get(pid, 0.0), h.excess_z)

    rows = []
    tally: dict[str, int] = defaultdict(int)
    for c in crimes:
        st = status_of(c, latest_month)
        tally[st] += 1
        key = c.persistent_id or str(c.id)
        base = coldness(c, latest_month)
        z = z_by_crime.get(key, 0.0)
        # Priority blends the case's own weight with where it sits. A weighted
        # sum, not a product, so neither term can swamp the other.
        priority = round(0.75 * base + 0.25 * 100 * min(z, 6.0) / 6.0, 1) if base else 0.0
        rows.append({
            **c.as_dict(),
            "status": st,
            "status_label": STATUS_LABEL[st],
            "age_months": months_between(c.month, latest_month),
            "score": base,
            "hotspot_z": round(z, 1),
            "priority": priority,
            "in_hotspot": key in z_by_crime,
        })

    # Filtering to a single category makes severity constant, so priority ties
    # in bulk and the order within a tie would otherwise be arbitrary. Break on
    # local excess, then on age - the two things that still distinguish them.
    rows.sort(key=lambda r: (-r["priority"], -r["hotspot_z"], -r["age_months"]))
    total = len(rows) or 1
    return {
        "latest_month": latest_month,
        "total": len(rows),
        "tally": dict(tally),
        "unsolved_pct": round(100 * (tally[COLD] + tally[STALLED]) / total, 1),
        "hotspots": [h.as_dict() for h in hotspots],
        "cases": rows,
    }
