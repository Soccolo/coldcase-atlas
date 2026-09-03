"""
Cold Case Atlas - Streamlit front end.

This is the deployable face of the project. It serves the two sources that are
explicitly licensed for public re-use and carry no personal data:

  * data.police.uk   - Open Government Licence v3.0
  * Eurostat         - Eurostat reuse policy

The Interpol layer is deliberately absent here. It displays named individuals
and reaches its API by impersonating a browser TLS fingerprint, neither of
which belongs on a public deployment - see atlas/config.py. It still works
locally via the FastAPI app with ATLAS_ENABLE_INTERPOL=1.
"""
from __future__ import annotations

import json

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from atlas import triage
from atlas.sources import eurostat as es
from atlas.sources import police_uk as police

st.set_page_config(page_title="Cold Case Atlas", page_icon="🔎",
                   layout="wide", initial_sidebar_state="expanded")

# Status palette. Validated for both surfaces with the dataviz validator:
# lightness band, chroma floor, CVD separation, normal-vision floor and
# contrast all pass. Status colour never carries meaning alone - every table
# and tooltip prints the status label beside it.
STATUS_COLOR = {
    triage.COLD:     "#1d4ed8",   # deep blue  - no suspect identified
    triage.STALLED:  "#b45309",   # amber      - suspect known, not prosecuted
    triage.UNKNOWN:  "#9333ea",   # violet     - no outcome published
    triage.OPEN:     "#0d9488",   # teal       - outcome pending
    triage.RESOLVED: "#94a3b8",   # muted grey - excluded from the map
}
ACCENT = "#1d4ed8"

CATEGORIES = {
    "All crime": "all-crime",
    "Violence and sexual offences": "violent-crime",
    "Robbery": "robbery",
    "Burglary": "burglary",
    "Criminal damage and arson": "criminal-damage-arson",
    "Possession of weapons": "possession-of-weapons",
    "Vehicle crime": "vehicle-crime",
    "Theft from the person": "theft-from-the-person",
}


# --------------------------------------------------------------------------
# Cached data access. Streamlit Cloud has an ephemeral filesystem, so the
# on-disk caches in the source modules do not survive a restart - these
# in-process caches are what actually keeps the app responsive.
# --------------------------------------------------------------------------

@st.cache_data(ttl=86400, show_spinner=False)
def get_latest_month() -> str:
    return police.last_updated()


@st.cache_data(ttl=3600, show_spinner=False)
def geocode(postcode: str) -> tuple[float, float, str]:
    return police.geocode_postcode(postcode)


@st.cache_data(ttl=3600, show_spinner=False)
def get_portfolio(lat: float, lng: float, category: str, months: int,
                  latest: str) -> dict:
    window = police.month_range(latest, months)
    crimes = police.crimes_over_months(lat, lng, window, category)
    data = triage.build_portfolio(crimes, latest)
    data["months_swept"] = len(window)
    return data


@st.cache_data(ttl=86400, show_spinner=False)
def get_eurostat(year: str, iccs: str) -> pd.DataFrame:
    rows = es.offences(year=year, iccs=iccs)
    df = pd.DataFrame([{"country": r.get("geo_label") or r.get("geo"),
                        "offences": r["value"]} for r in rows if r.get("value")])
    return df.sort_values("offences", ascending=False) if not df.empty else df


@st.cache_data(ttl=86400, show_spinner=False)
def get_years() -> list[str]:
    return es.available_years()


# --------------------------------------------------------------------------
# Map
# --------------------------------------------------------------------------
# Neither built-in option draws a basemap here: st.map needs a Mapbox token,
# and pydeck's TileLayer cannot be expressed through the JSON converter, so
# no tiles are ever requested. Leaflet with plain OSM tiles needs no key and
# no extra dependency, so the map is a small self-contained component.

def render_map(plot: pd.DataFrame, hotspots: list[dict],
               lat: float, lng: float, height: int = 460) -> None:
    points = [{
        "lat": r.lat, "lng": r.lng, "c": STATUS_COLOR[r.status],
        "r": 3 + r.priority * 0.07,
        "t": f"{r.street} — {r.status_label} — {r.month} · priority {r.priority}",
    } for r in plot.itertuples()]

    rings = [{
        "lat": h["centre"]["lat"], "lng": h["centre"]["lng"],
        "rad": max(h["cell_m"] * 0.75, 90),
        "t": (f"{h['ratio']}x the surrounding area — {h['observed']} unsolved "
              f"where {h['expected']} expected (z={h['excess_z']})"),
    } for h in hotspots]

    html = f"""
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<div id="m" style="height:{height - 10}px;width:100%;border-radius:8px;overflow:hidden"></div>
<script>
// scrollWheelZoom off so the page still scrolls when the cursor crosses the
// map; click once to zoom with the wheel, as embedded maps conventionally do.
const map = L.map('m', {{scrollWheelZoom:false}}).setView([{lat}, {lng}], 13);
map.on('click', () => map.scrollWheelZoom.enable());
map.on('mouseout', () => map.scrollWheelZoom.disable());
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution:'&copy; OpenStreetMap contributors', maxZoom:19, opacity:0.85
}}).addTo(map);
{json.dumps(rings)}.forEach(h => L.circle([h.lat,h.lng], {{
  radius:h.rad, color:'#b45309', weight:1, fillColor:'#b45309', fillOpacity:.10
}}).addTo(map).bindPopup(h.t));
{json.dumps(points)}.forEach(p => L.circleMarker([p.lat,p.lng], {{
  radius:p.r, color:'#ffffff', weight:1, fillColor:p.c, fillOpacity:.85
}}).addTo(map).bindPopup(p.t));
// Streamlit sizes the component iframe after the script runs, so Leaflet
// initialises against a container of the wrong width and tiles only cover
// part of it. Re-measure once the frame has settled.
setTimeout(() => map.invalidateSize(), 150);
setTimeout(() => map.invalidateSize(), 600);
window.addEventListener('resize', () => map.invalidateSize());
</script>"""
    components.html(html, height=height)


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------

st.sidebar.title("🔎 Cold Case Atlas")
st.sidebar.caption("Unsolved-case intelligence from official open data")

postcode = st.sidebar.text_input("UK postcode", value="L1 8JQ",
                                 help="Anywhere in England, Wales or Northern Ireland")
category_label = st.sidebar.selectbox("Offence category", list(CATEGORIES),
                                      index=1)
months = st.sidebar.select_slider("Months to sweep", [6, 12, 18, 24, 36, 48, 60],
                                  value=24)
st.sidebar.divider()
st.sidebar.caption(
    "Contains public sector information licensed under the Open Government "
    "Licence v3.0. Source: data.police.uk. Statistics from Eurostat."
)

tab_uk, tab_eu, tab_about = st.tabs(
    ["UK case portfolio", "European context", "How to read this"])


# --------------------------------------------------------------------------
# UK portfolio
# --------------------------------------------------------------------------

with tab_uk:
    try:
        latest = get_latest_month()
        lat, lng, place = geocode(postcode)
    except police.PoliceUKError as e:
        st.error(f"Could not look that up: {e}")
        st.stop()

    with st.spinner(f"Sweeping {months} months of police records around {place}…"):
        try:
            port = get_portfolio(lat, lng, CATEGORIES[category_label], months, latest)
        except police.PoliceUKError as e:
            st.error(f"data.police.uk is not responding: {e}")
            st.stop()

    tally = port["tally"]
    st.subheader(f"{place} · {category_label.lower()}")
    st.caption(f"{port['months_swept']} months to {port['latest_month']} · "
               f"records within roughly one mile")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Records", f"{port['total']:,}")
    c2.metric("Cold · no suspect", f"{tally.get(triage.COLD, 0):,}")
    c3.metric("Stalled", f"{tally.get(triage.STALLED, 0):,}")
    c4.metric("Unsolved", f"{port['unsolved_pct']}%")
    c5.metric("Hotspots", len(port["hotspots"]))

    cases = pd.DataFrame(port["cases"])
    if cases.empty:
        st.info("No records returned for that combination.")
        st.stop()

    # ---- Map -------------------------------------------------------------
    st.markdown("#### Where the unsolved cases are")
    unsolved = cases[(cases["status"] != triage.RESOLVED) & cases["lat"].notna()]
    # A dense city returns 10k+ records and plotting them all produces a solid
    # blob that says nothing. Cap to the highest-priority slice - the map is
    # meant to show what to look at, not to prove the data is big.
    MAP_CAP = 400
    plot = unsolved.nlargest(MAP_CAP, "priority").copy()
    if not plot.empty:
        render_map(plot, port["hotspots"], lat, lng)
        if len(unsolved) > MAP_CAP:
            st.caption(f"Showing the {MAP_CAP} highest-priority of "
                       f"{len(unsolved):,} unsolved records.")
        legend = " · ".join(
            f":{'blue' if s == triage.COLD else 'orange' if s == triage.STALLED else 'violet' if s == triage.UNKNOWN else 'green'}"
            f"[●] {triage.STATUS_LABEL[s]}"
            for s in (triage.COLD, triage.STALLED, triage.UNKNOWN, triage.OPEN)
            if s in set(plot["status"]))
        st.caption(legend + "  ·  marker size follows priority")
    else:
        st.info("Nothing mappable in this selection.")

    # ---- Hotspots --------------------------------------------------------
    if port["hotspots"]:
        st.markdown("#### Hotspots — unsolved crime above the local baseline")
        hs = pd.DataFrame(port["hotspots"])
        hs["streets"] = hs["streets"].apply(lambda s: ", ".join(s[:3]))
        st.dataframe(
            hs[["ratio", "observed", "expected", "excess_z", "cell_m",
                "from_month", "to_month", "streets"]]
              .rename(columns={"ratio": "× local baseline", "observed": "Observed",
                               "expected": "Expected", "excess_z": "z",
                               "cell_m": "Cell (m)", "from_month": "From",
                               "to_month": "To", "streets": "Streets"}),
            width="stretch", hide_index=True)
        st.caption(
            "Each 200 m cell is compared with the ring of cells around it, so this "
            "is excess over the **local** baseline rather than raw density — "
            "otherwise every city centre lights up. It remains a pattern in "
            "*reported* data, not evidence that offences are linked, and "
            "police.uk snaps each record to an anonymised map point, so a cell "
            "can look busy because a snap point sits inside it. Treat a hotspot "
            "as a question, never a finding.")

    # ---- Ranked cases ----------------------------------------------------
    st.markdown("#### Ranked by priority")
    show = cases[cases["priority"] > 0].head(200)
    st.dataframe(
        show[["priority", "score", "status_label", "street", "month",
              "age_months", "hotspot_z"]]
          .rename(columns={"priority": "Priority", "score": "Case score",
                           "status_label": "Status", "street": "Location",
                           "month": "Month", "age_months": "Age (months)",
                           "hotspot_z": "Hotspot z"}),
        width="stretch", hide_index=True,
        column_config={"Priority": st.column_config.ProgressColumn(
            "Priority", min_value=0, max_value=100, format="%.1f")})
    st.caption("Priority = severity × unsolved status × age, blended with local "
               "hotspot excess. Every term is in `atlas/triage.py` and arguable.")


# --------------------------------------------------------------------------
# European context
# --------------------------------------------------------------------------

with tab_eu:
    st.subheader("Police-recorded offences across Europe")
    st.caption("Eurostat `crim_off_cat` — 41 countries, ICCS categories")

    try:
        years = get_years()
    except es.EurostatError as e:
        st.error(f"Eurostat is not responding: {e}")
        st.stop()

    col_a, col_b = st.columns([1, 2])
    year = col_a.selectbox("Year", list(reversed(years)), index=2)
    offence_label = col_b.selectbox("Offence", list(es.SERIOUS.values()), index=0)
    iccs = next(k for k, v in es.SERIOUS.items() if v == offence_label)

    with st.spinner("Fetching Eurostat…"):
        try:
            df = get_eurostat(year, iccs)
        except es.EurostatError as e:
            st.error(f"Eurostat: {e}")
            st.stop()

    if df.empty:
        st.info("Eurostat has no figures for that combination.")
    else:
        top = df.head(25)
        # One series, one hue: magnitude, not identity. No legend - the title
        # names the measure - and values sit in text ink beside each bar.
        chart = (
            alt.Chart(top)
            .mark_bar(cornerRadiusEnd=4, size=14, color=ACCENT)
            .encode(
                x=alt.X("offences:Q", title=None,
                        axis=alt.Axis(grid=True, gridOpacity=0.25, format="~s")),
                y=alt.Y("country:N", sort="-x", title=None),
                tooltip=[alt.Tooltip("country:N", title="Country"),
                         alt.Tooltip("offences:Q", title=offence_label,
                                     format=",.0f")],
            )
            .properties(height=max(320, 22 * len(top)))
        )
        labels = chart.mark_text(align="left", dx=6, fontSize=11,
                                 color="#475569").encode(
            text=alt.Text("offences:Q", format=",.0f"))
        st.altair_chart(chart + labels, width="stretch")

        st.caption(
            f"**{offence_label}, {year}.** Raw counts, not population-adjusted — "
            "a comparison between Germany and Malta says more about population "
            "than about crime. Recording practice also differs by country, so "
            "these are national reporting figures rather than like-for-like rates.")
        with st.expander("Table view"):
            st.dataframe(df.rename(columns={"country": "Country",
                                            "offences": offence_label}),
                         width="stretch", hide_index=True)


# --------------------------------------------------------------------------
# About
# --------------------------------------------------------------------------

with tab_about:
    st.subheader("How to read this")
    st.markdown("""
**Case status.** `data.police.uk` publishes a free-text outcome per record. The
useful distinction is buried in it:

| Status | Meaning |
|---|---|
| **Cold** | Investigation complete, **no suspect identified** — the real cold case |
| **Stalled** | Suspect known, prosecution failed or was dropped |
| **Open** | Outcome still pending |
| **No outcome published** | The force was silent — not the same as resolved |
| **Resolved** | Charged, cautioned, sentenced or otherwise disposed |

A record older than twelve months with no outcome is treated as cold rather
than pending, because forces run roughly two months behind.

**What this app deliberately does not do.** It carries no personal data. It
names nobody, and it hosts no community speculation about who did what.

That is not squeamishness, it is the constraint the whole design is built
around. Aggregating forum accusations about unsolved cases runs into UK/EU
**GDPR Article 10** — offence-related personal data may only be processed under
official authority or where authorised by law — and the **Defamation Act
2013**, where repeating someone else's allegation makes you a publisher of it.
Reddit's misidentification of Sunil Tripathi after the Boston Marathon bombing,
and the professor wrongly named during the Idaho student murders, are what that
costs an innocent person.

The repository does contain a community layer for local use. It masks every
person name the police did not themselves publish, and drops any passage that
reads as an accusation rather than redacting it — a redacted accusation is
still an accusation with a blank where the name goes.

**Known limits.** police.uk snaps every record to an anonymised map point on a
street segment rather than a real address, which is the biggest caveat on the
hotspot view. Scotland is absent — Police Scotland does not publish here.
Eurostat counts are raw and not population-adjusted.

**If you want to actually help.** This app reads published records; it does not
investigate. [Locate International](https://locate.international) is a UK
charity that trains volunteers to run genuine cold case reviews on unidentified
bodies and long-term missing persons, alongside police. If you have information
on a case, it goes to the force or Crimestoppers on 0800 555 111 — never a
forum.
    """)
