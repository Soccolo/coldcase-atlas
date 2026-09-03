"""
data.police.uk client.

Open Government Licence v3. No API key, no rate limit published (be polite).
Covers England, Wales and Northern Ireland. Scotland is NOT in this dataset
(Police Scotland does not publish to police.uk) - see README for that gap.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Iterable

import requests

BASE = "https://data.police.uk/api"
UA = "ColdCaseAtlas/0.1 (personal research tool)"

_session = requests.Session()
_session.headers["User-Agent"] = UA


class PoliceUKError(RuntimeError):
    pass


def _get(path: str, **params) -> object:
    """GET with one polite retry on 429/5xx."""
    url = f"{BASE}/{path.lstrip('/')}"
    for attempt in range(3):
        r = _session.get(url, params=params or None, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return []
        if r.status_code in (429, 502, 503, 504) and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        raise PoliceUKError(f"{r.status_code} for {r.url}: {r.text[:200]}")
    return []


def last_updated() -> str:
    """Latest month present in the dataset, as 'YYYY-MM'."""
    return _get("crime-last-updated")["date"][:7]


def forces() -> list[dict]:
    return _get("forces")


def categories(month: str) -> list[dict]:
    return _get("crime-categories", date=month)


@dataclass
class Crime:
    """One record as published by police.uk. No personal data - by design."""
    id: int
    persistent_id: str
    category: str
    month: str
    lat: float | None
    lng: float | None
    street: str
    outcome: str | None
    context: str

    @classmethod
    def from_api(cls, d: dict) -> "Crime":
        loc = d.get("location") or {}
        street = ((loc.get("street") or {}).get("name") or "").replace("On or near ", "")
        out = (d.get("outcome_status") or {}).get("category")
        return cls(
            id=d.get("id") or 0,
            persistent_id=d.get("persistent_id") or "",
            category=d.get("category") or "unknown",
            month=d.get("month") or "",
            lat=float(loc["latitude"]) if loc.get("latitude") else None,
            lng=float(loc["longitude"]) if loc.get("longitude") else None,
            street=street or "location withheld",
            outcome=out,
            context=d.get("context") or "",
        )

    def as_dict(self) -> dict:
        return asdict(self)


def crimes_at_point(lat: float, lng: float, month: str,
                    category: str = "all-crime") -> list[Crime]:
    """Crimes within a ~1 mile radius of a point, for one month."""
    raw = _get(f"crimes-street/{category}", lat=lat, lng=lng, date=month)
    return [Crime.from_api(d) for d in raw]


def crimes_over_months(lat: float, lng: float, months: Iterable[str],
                       category: str = "all-crime", workers: int = 6) -> list[Crime]:
    """
    Sweep several months and deduplicate on persistent_id.

    One request per month, so a 36-month sweep is 36 round trips - slow enough
    to be felt on a hosted page. A small thread pool cuts that to a couple of
    seconds; police.uk publishes no rate limit, so the pool size is the
    politeness budget.
    """
    months = list(months)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        batches = pool.map(lambda m: crimes_at_point(lat, lng, m, category), months)

    seen: set[str] = set()
    out: list[Crime] = []
    for batch in batches:
        for c in batch:
            key = c.persistent_id or f"{c.id}"
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
    return out


def outcomes_for_crime(persistent_id: str) -> dict:
    """Full outcome timeline for one crime - the closest thing to a case file."""
    return _get(f"outcomes-for-crime/{persistent_id}") or {}


def month_range(latest: str, back: int) -> list[str]:
    """['2026-06', '2026-05', ...] going `back` months from `latest`."""
    y, m = (int(x) for x in latest.split("-"))
    months = []
    for _ in range(back):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return months


def geocode_postcode(postcode: str) -> tuple[float, float, str]:
    """UK postcode -> (lat, lng, tidy name) via postcodes.io. Free, no key."""
    pc = postcode.strip().replace(" ", "")
    r = _session.get(f"https://api.postcodes.io/postcodes/{pc}", timeout=20)
    if r.status_code != 200:
        raise PoliceUKError(f"Unknown postcode: {postcode}")
    res = r.json()["result"]
    name = f"{res['postcode']} ({res.get('admin_district') or res.get('region')})"
    return res["latitude"], res["longitude"], name
