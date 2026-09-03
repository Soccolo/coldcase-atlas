"""
Eurostat - the pan-European statistical layer.

Interpol gives case-level records; this gives the denominator. `crim_off_cat`
is police-recorded offences by ICCS category for 41 countries, which is what
lets you say whether a national figure is unusual rather than just large.

One gotcha worth recording: this API is reachable from Python and NOT from the
`curl` shipped with Git for Windows. That build is from 2021 and its CA bundle
no longer verifies the Europa certificate chain, so curl fails the TLS
handshake and reports a bare connection error. It looks exactly like the API
being down. It is not - `requests` uses certifi and works fine.

Returns JSON-stat 2.0, which is a flat value array plus dimension indices,
so it needs decoding rather than plain dict access.
"""
from __future__ import annotations

import requests

BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"

# Datasets worth having. Eurostat ids are opaque, so they are named here.
DATASETS = {
    "offences": "crim_off_cat",       # police-recorded offences by category
    "homicide": "crim_hom_vrel",      # homicide by victim-offender relationship
    "prisons": "crim_pris_cap",       # prison capacity and population
}

# ICCS codes that matter for a cold-case view.
SERIOUS = {
    "ICCS0101": "Intentional homicide",
    "ICCS0102": "Attempted intentional homicide",
    "ICCS020221": "Kidnapping",
    "ICCS0301": "Sexual violence",
    "ICCS03011": "Rape",
    "ICCS03012": "Sexual assault",
    "ICCS0401": "Robbery",
    "ICCS05012": "Burglary of private residential premises",
}


class EurostatError(RuntimeError):
    pass


def _fetch(dataset: str, **params) -> dict:
    params.setdefault("format", "JSON")
    params.setdefault("lang", "EN")
    r = requests.get(f"{BASE}/{dataset}", params=params, timeout=45)
    if r.status_code != 200:
        raise EurostatError(f"Eurostat returned {r.status_code} for {dataset}")
    return r.json()


def _decode(js: dict) -> list[dict]:
    """
    Flatten JSON-stat 2.0 into plain rows.

    The value array is a sparse dict keyed by a flat index; recovering the
    dimension coordinates means dividing that index down through the strides.
    """
    ids = js["id"]
    sizes = js["size"]
    dims = js["dimension"]

    # Position -> code, per dimension.
    decoders = []
    for d in ids:
        idx = dims[d]["category"]["index"]
        if isinstance(idx, dict):
            codes = sorted(idx, key=lambda k: idx[k])
        else:
            codes = list(idx)
        labels = dims[d]["category"].get("label", {})
        decoders.append((d, codes, labels))

    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]

    rows = []
    for flat, val in js.get("value", {}).items():
        n = int(flat)
        row = {}
        for (name, codes, labels), stride in zip(decoders, strides):
            pos = (n // stride) % len(codes)
            code = codes[pos]
            row[name] = code
            row[f"{name}_label"] = labels.get(code, code)
        row["value"] = val
        rows.append(row)
    return rows


def offences(year: str | None = None, iccs: str | None = None) -> list[dict]:
    """
    Police-recorded offences by country, offence type and year.

    Counts are per country and not population-adjusted - a raw comparison
    between Germany and Malta says more about population than about crime.
    """
    params = {}
    if year:
        params["time"] = year
    if iccs:
        params["iccs"] = iccs
    rows = _decode(_fetch(DATASETS["offences"], **params))
    return [r for r in rows if r.get("unit") in (None, "NR")]


def serious_by_country(year: str = "2022") -> dict:
    """Serious-offence counts per country for one year, ready for a table."""
    out: dict[str, dict] = {}
    for code, label in SERIOUS.items():
        try:
            rows = offences(year=year, iccs=code)
        except EurostatError:
            continue
        for r in rows:
            geo = r.get("geo_label") or r.get("geo")
            out.setdefault(geo, {"country": geo})[label] = r["value"]
    return {"year": year, "offences": list(SERIOUS.values()),
            "rows": sorted(out.values(), key=lambda x: x["country"])}


def available_years() -> list[str]:
    """Years present in the offences dataset."""
    js = _fetch(DATASETS["offences"], iccs="ICCS0101")
    idx = js["dimension"]["time"]["category"]["index"]
    return sorted(idx, key=lambda k: idx[k]) if isinstance(idx, dict) else list(idx)
