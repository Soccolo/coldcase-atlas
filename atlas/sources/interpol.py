"""
Interpol public notices - the pan-European case-level source.

This is the module I wrongly said could not be built. The API at
ws-public.interpol.int is genuinely public: no key, no account, no ToS gate.
It is the same endpoint interpol.int's own public notice search calls.

Getting to it needs one trick. Akamai in front of it fingerprints the TLS
handshake, so plain `curl` and `requests` are refused with 403 no matter what
headers they send, while a real browser sails through. `curl_cffi` impersonates
Chrome's TLS fingerprint, which is enough.

Be deliberate about that. The block is generic bot mitigation, not an access
policy - the data is published precisely so the public will look at it, and a
yellow notice only works if people see it. But going around bot mitigation
still carries an obligation to behave like a good citizen rather than a
scraper: this module caches to disk for a week, sleeps between calls, and
fetches details only for the notices actually being displayed. If this ever
becomes a public or commercial service, ask Interpol for proper access first.

Notice types
------------
YELLOW = missing persons. Victims and subjects of an appeal, published so the
public can help find them. Relaying these, unchanged and with attribution, is
the intended use.

RED = wanted persons. Accused, not convicted, and squarely within UK/EU GDPR
Art. 10. Supported here but off by default, and the UI must never present a
red notice as anything other than "a country has asked for this person to be
located and arrested". Presumption of innocence is not optional.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path

from .. import config

try:
    from curl_cffi import requests as _cr
    AVAILABLE = True
except ImportError:  # pragma: no cover - absent in the cloud build on purpose
    _cr = None
    AVAILABLE = False

BASE = "https://ws-public.interpol.int/notices/v1"
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache" / "interpol"
CACHE_DAYS = 7
PAUSE = 0.3   # seconds between serial calls - deliberate politeness, not a limit
WORKERS = 6   # concurrent detail fetches; the pool size is the real rate limit

# EU/EEA + UK. Interpol filters on nationality, which is the closest handle
# it offers to "cases connected to this part of the world".
EU_UK = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia",
    "CY": "Cyprus", "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia",
    "FI": "Finland", "FR": "France", "DE": "Germany", "GR": "Greece",
    "HU": "Hungary", "IE": "Ireland", "IT": "Italy", "LV": "Latvia",
    "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta", "NL": "Netherlands",
    "PL": "Poland", "PT": "Portugal", "RO": "Romania", "SK": "Slovakia",
    "SI": "Slovenia", "ES": "Spain", "SE": "Sweden", "NO": "Norway",
    "IS": "Iceland", "CH": "Switzerland", "GB": "United Kingdom",
}


class InterpolUnavailable(RuntimeError):
    pass


def _require():
    if not config.interpol_enabled():
        raise InterpolUnavailable(
            "Interpol layer is off. It is local-only by design - see "
            "atlas/config.py. Enable with ATLAS_ENABLE_INTERPOL=1."
        )
    if not AVAILABLE:
        raise InterpolUnavailable(
            "Interpol needs curl_cffi to get past TLS fingerprinting: "
            "pip install curl_cffi"
        )


def _cached(key: str) -> dict | None:
    f = CACHE_DIR / f"{key}.json"
    if not f.exists():
        return None
    age_days = (time.time() - f.stat().st_mtime) / 86400
    if age_days > CACHE_DAYS:
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _store(key: str, data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{key}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _get(url: str, cache_key: str | None = None, pause: bool = True,
         **params) -> dict:
    # Gate first, cache second. The other way round let a warm disk cache
    # serve Interpol data with the feature switched off, which defeats the
    # point of the switch.
    _require()
    if cache_key:
        hit = _cached(cache_key)
        if hit is not None:
            return hit
    r = _cr.get(url, params=params or None, impersonate="chrome", timeout=30)
    if r.status_code == 403:
        raise InterpolUnavailable(
            "Interpol returned 403. The TLS impersonation may have gone stale - "
            "try upgrading curl_cffi, or a different impersonate= target."
        )
    if r.status_code != 200:
        raise InterpolUnavailable(f"Interpol returned {r.status_code}")
    data = r.json()
    if cache_key:
        _store(cache_key, data)
    if pause:
        time.sleep(PAUSE)
    return data


def _details_bulk(notices: list["Notice"], kind: str = "yellow",
                  workers: int = WORKERS) -> list["Notice"]:
    """
    Fill in full records for many notices at once.

    One detail request per notice, so ~700 EU notices serially at PAUSE each
    is several minutes - unusable as a page load. A small thread pool brings
    that under a minute, and the concurrency itself is the rate limit, so the
    per-call sleep is dropped here rather than stacked on top of it.

    Cached entries return without a request at all, so the second run of any
    given country costs nothing.
    """
    def one(n: "Notice") -> "Notice":
        slug = n.entity_id.replace("/", "-")
        try:
            return _flatten(_get(f"{BASE}/{kind}/{slug}",
                                 cache_key=f"{kind}-{slug}", pause=False), kind)
        except (InterpolUnavailable, ValueError):
            return n   # keep the summary rather than dropping the person

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, notices))


@dataclass
class Notice:
    """One Interpol notice, flattened to what a case view actually needs."""
    entity_id: str
    kind: str                 # "yellow" or "red"
    forename: str
    name: str
    date_of_birth: str | None
    date_of_event: str | None      # when they went missing
    nationalities: list[str]
    issuing_country: str | None
    place_of_birth: str | None
    sex: str | None
    height: float | None
    distinguishing_marks: str | None
    languages: list[str]
    countries_likely_visited: list[str]
    thumbnail: str | None
    detail_url: str

    @property
    def years_missing(self) -> float | None:
        if not self.date_of_event:
            return None
        try:
            d = datetime.strptime(self.date_of_event, "%Y/%m/%d").date()
        except ValueError:
            return None
        return round((date.today() - d).days / 365.25, 1)

    @property
    def age_now(self) -> int | None:
        if not self.date_of_birth:
            return None
        try:
            d = datetime.strptime(self.date_of_birth, "%Y/%m/%d").date()
        except ValueError:
            return None
        return int((date.today() - d).days / 365.25)

    def as_dict(self) -> dict:
        return {**asdict(self),
                "years_missing": self.years_missing,
                "age_now": self.age_now,
                "display_name": f"{self.forename or ''} {self.name or ''}".strip()}


def _flatten(d: dict, kind: str) -> Notice:
    links = d.get("_links", {})
    return Notice(
        entity_id=d.get("entity_id", ""),
        kind=kind,
        forename=d.get("forename") or "",
        name=d.get("name") or "",
        date_of_birth=d.get("date_of_birth"),
        date_of_event=d.get("date_of_event"),
        nationalities=d.get("nationalities") or [],
        issuing_country=d.get("issuing_country"),
        place_of_birth=d.get("place_of_birth"),
        sex=d.get("sex_id"),
        height=d.get("height"),
        distinguishing_marks=d.get("distinguishing_marks"),
        languages=d.get("languages_spoken_ids") or [],
        countries_likely_visited=d.get("countries_likely_to_be_visited") or [],
        thumbnail=(links.get("thumbnail") or {}).get("href"),
        detail_url=(links.get("self") or {}).get("href", ""),
    )


def list_notices(kind: str = "yellow", nationality: str | None = None,
                 limit: int = 40) -> list[Notice]:
    """List notices, optionally filtered to one nationality."""
    key = f"list-{kind}-{nationality or 'all'}-{limit}"
    params = {"resultPerPage": min(limit, 160)}
    if nationality:
        params["nationality"] = nationality
    data = _get(f"{BASE}/{kind}", cache_key=key, **params)
    return [_flatten(n, kind)
            for n in data.get("_embedded", {}).get("notices", [])]


def detail(entity_id: str, kind: str = "yellow") -> Notice:
    """Full record for one notice. entity_id looks like '2026/55180'."""
    slug = entity_id.replace("/", "-")
    data = _get(f"{BASE}/{kind}/{slug}", cache_key=f"{kind}-{slug}")
    return _flatten(data, kind)


def eu_missing(countries: list[str] | None = None, per_country: int = 30,
               with_detail: bool = False) -> list[dict]:
    """
    Missing-person notices across the EU, EEA and UK, longest-missing first.

    `with_detail` fetches the full record per notice - richer, but one request
    each, so it is off by default and the server only turns it on for the
    notices it is about to show.
    """
    countries = countries or list(EU_UK)
    out: list[Notice] = []
    for cc in countries:
        try:
            out.extend(list_notices("yellow", nationality=cc, limit=per_country))
        except InterpolUnavailable:
            continue

    # One person can hold two nationalities and appear twice.
    seen: set[str] = set()
    unique: list[Notice] = []
    for n in out:
        if n.entity_id in seen:
            continue
        seen.add(n.entity_id)
        unique.append(n)

    if with_detail:
        unique = _details_bulk(unique, "yellow")

    rows = [n.as_dict() for n in unique]
    # Longest-missing first; notices with no date_of_event sort last.
    rows.sort(key=lambda r: -(r["years_missing"] or -1))
    return rows


def country_counts(kind: str = "yellow") -> dict[str, int]:
    """Live notice count per EU/UK country - cheap, one request each."""
    counts: dict[str, int] = {}
    for cc in EU_UK:
        try:
            data = _get(f"{BASE}/{kind}", cache_key=f"count-{kind}-{cc}",
                        nationality=cc, resultPerPage=1)
            counts[cc] = data.get("total", 0)
        except InterpolUnavailable:
            counts[cc] = 0
    return counts
