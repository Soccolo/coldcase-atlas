"""FastAPI server. Official data on the main path, community data quarantined."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from . import triage
from .sources import community, interpol
from .sources import police_uk as police

WEB = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Cold Case Atlas", version="0.1.0")


def _interpol_state() -> str:
    """One-line status for the Interpol layer, used by /api/health."""
    if not interpol.AVAILABLE:
        return "needs curl_cffi (pip install curl_cffi)"
    try:
        interpol.list_notices("yellow", nationality="IE", limit=1)
        return "ok"
    except interpol.InterpolUnavailable as exc:
        return f"unavailable: {exc}"


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
def health():
    out = {"police_uk": "unknown", "interpol": _interpol_state(),
           "reddit": "not configured", "latest_month": None}
    try:
        out["latest_month"] = police.last_updated()
        out["police_uk"] = "ok"
    except Exception as e:
        out["police_uk"] = f"error: {type(e).__name__}"
    if community.configured():
        try:
            community.search("test", limit=1)
            out["reddit"] = "ok"
        except Exception as e:
            out["reddit"] = f"error: {e}"
    return out


@app.get("/api/portfolio")
def portfolio(
    postcode: str | None = Query(None, description="UK postcode, e.g. M1 1AE"),
    lat: float | None = None,
    lng: float | None = None,
    months: int = Query(24, ge=1, le=60),
    category: str = Query("all-crime"),
):
    """Ranked unsolved-case portfolio around a point."""
    place = f"{lat}, {lng}"
    if postcode:
        try:
            lat, lng, place = police.geocode_postcode(postcode)
        except police.PoliceUKError as e:
            raise HTTPException(400, str(e))
    if lat is None or lng is None:
        raise HTTPException(400, "Give a postcode, or lat and lng.")

    try:
        latest = police.last_updated()
        window = police.month_range(latest, months)
        crimes = police.crimes_over_months(lat, lng, window, category)
    except police.PoliceUKError as e:
        raise HTTPException(502, f"police.uk: {e}")

    data = triage.build_portfolio(crimes, latest)
    data.update({
        "place": place,
        "centre": {"lat": lat, "lng": lng},
        "months_swept": len(window),
        "category": category,
        "licence": "Contains public sector information licensed under the Open "
                   "Government Licence v3.0. Source: data.police.uk",
    })
    return JSONResponse(data)


@app.get("/api/case/{persistent_id}")
def case_detail(persistent_id: str, street: str = "", area: str = "",
                with_community: bool = False):
    """
    One case: its official outcome timeline, plus - only if explicitly asked
    for - screened community context.
    """
    try:
        timeline = police.outcomes_for_crime(persistent_id)
    except police.PoliceUKError as e:
        raise HTTPException(502, f"police.uk: {e}")

    out = {
        "persistent_id": persistent_id,
        "official": timeline,
        "community": [],
        "community_status": "not requested",
    }

    if with_community:
        if not community.configured():
            out["community_status"] = (
                "Reddit not configured - add REDDIT_CLIENT_ID and "
                "REDDIT_CLIENT_SECRET to .env"
            )
        else:
            try:
                items = community.context_for_case(street, area, "", None)
                out["community"] = items
                out["community_status"] = (
                    f"{len(items)} screened items. Unverified public discussion, "
                    "shown as context only. Names not published by police are masked."
                )
            except community.RedditUnavailable as e:
                out["community_status"] = f"unavailable: {e}"

    return JSONResponse(out)


@app.get("/api/eu/missing")
def eu_missing(
    countries: str = Query("", description="Comma-separated ISO codes, blank = all EU/UK"),
    per_country: int = Query(25, ge=1, le=160),
    detail: bool = Query(True, description="Fetch full records (slower, cached 7 days)"),
):
    """
    Long-term missing persons across the EU, EEA and UK, from Interpol yellow
    notices. Longest-missing first.

    These are official appeals published so the public will see them, so names
    and photographs pass through unchanged and attributed. That is the opposite
    of the community layer, where names are masked - the distinction is who
    published the name and why.
    """
    if not interpol.AVAILABLE:
        raise HTTPException(503, "Interpol layer needs curl_cffi: pip install curl_cffi")

    codes = [c.strip().upper() for c in countries.split(",") if c.strip()] or None
    try:
        rows = interpol.eu_missing(countries=codes, per_country=per_country,
                                   with_detail=detail)
    except interpol.InterpolUnavailable as e:
        raise HTTPException(502, str(e))

    return JSONResponse({
        "count": len(rows),
        "countries": codes or list(interpol.EU_UK),
        "notices": rows,
        "source": "Interpol public yellow notices (ws-public.interpol.int)",
        "note": "Official missing-person appeals, republished unchanged. "
                "If you recognise someone, contact your national police or "
                "Interpol - not a forum.",
    })


@app.get("/api/eu/counts")
def eu_counts(kind: str = Query("yellow", pattern="^(yellow|red)$")):
    """Live notice count per EU/UK country."""
    if not interpol.AVAILABLE:
        raise HTTPException(503, "Interpol layer needs curl_cffi")
    try:
        counts = interpol.country_counts(kind)
    except interpol.InterpolUnavailable as e:
        raise HTTPException(502, str(e))
    return {"kind": kind,
            "counts": [{"code": k, "country": interpol.EU_UK[k], "total": v}
                       for k, v in sorted(counts.items(), key=lambda x: -x[1])]}


@app.get("/api/categories")
def categories():
    try:
        latest = police.last_updated()
        return police.categories(latest)
    except police.PoliceUKError as e:
        raise HTTPException(502, str(e))
