"""
Cold Case Atlas - Streamlit front end.

Serves the two sources licensed for public re-use that carry no personal data:
data.police.uk (Open Government Licence v3.0) and Eurostat. The Interpol layer
is local-only - see atlas/config.py for why.

On the look: this is a records-office dossier, not a thriller. The data is
about real victims of real offences, so the register is archive-serious -
manila, typewriter refs, rubber-stamp status marks - and never lurid. Most of
the character lives in this file's own HTML components rather than in overrides
of Streamlit's chrome, so a Streamlit upgrade that renames an internal class
degrades the frame and leaves the substance intact.
"""
from __future__ import annotations

import html
import json

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from atlas import glossary, triage
from atlas.sources import eurostat as es
from atlas.sources import police_uk as police

st.set_page_config(page_title="Cold Case Atlas", page_icon="🗂",
                   layout="wide", initial_sidebar_state="expanded")

# Status palette, validated with the dataviz validator against this page's
# manila surface (#efe9dc): lightness band, chroma floor, CVD separation,
# normal-vision floor and contrast all pass. Colour never carries meaning
# alone - every stamp, row and tooltip prints the status in words beside it.
STATUS_COLOR = {
    triage.COLD:     "#1d4ed8",
    triage.STALLED:  "#b45309",
    triage.UNKNOWN:  "#9333ea",
    triage.OPEN:     "#0d9488",
    triage.RESOLVED: "#8a8578",
}
PAPER, CARD, INK, INK_DIM, RULE = "#efe9dc", "#f7f3e9", "#26221c", "#6b6558", "#c8bfa8"

CATEGORIES = {
    "Violence and sexual offences": "violent-crime",
    "All crime": "all-crime",
    "Robbery": "robbery",
    "Burglary": "burglary",
    "Criminal damage and arson": "criminal-damage-arson",
    "Possession of weapons": "possession-of-weapons",
    "Vehicle crime": "vehicle-crime",
    "Theft from the person": "theft-from-the-person",
}

st.markdown(f"""<style>
.stApp {{ background:{PAPER}; }}
.stApp, .stApp p, .stApp li, .stApp label {{ color:{INK}; }}
[data-testid="stHeader"] {{ background:transparent; }}
.stTabs [data-baseweb="tab-list"] {{ background:transparent; gap:26px; }}
.stTabs [data-baseweb="tab-highlight"] {{ background:{INK}; }}
.stTabs [data-baseweb="tab-border"] {{ background:{RULE}; }}
[data-testid="stSidebar"] {{ background:#e6dfd0; border-right:1px solid {RULE}; }}
[data-testid="stMetricValue"] {{ font-family:Georgia,'Times New Roman',serif;
    font-weight:600; color:{INK}; }}
[data-testid="stMetricLabel"] {{ text-transform:uppercase; letter-spacing:.09em;
    font-size:10px !important; color:{INK_DIM}; }}
.stTabs [data-baseweb="tab"] {{ font-family:'Courier New',monospace;
    text-transform:uppercase; letter-spacing:.11em; font-size:12px; }}
h1,h2,h3,h4 {{ font-family:Georgia,'Times New Roman',serif !important;
    color:{INK} !important; letter-spacing:.01em; }}
hr {{ border-color:{RULE}; }}
.dossier-head {{ font-family:'Courier New',monospace; font-size:11px;
    letter-spacing:.22em; text-transform:uppercase; color:{INK_DIM};
    border-bottom:2px solid {INK}; padding-bottom:5px; margin-bottom:3px; }}
.dossier-sub {{ font-family:Georgia,serif; font-size:25px; margin:6px 0 2px; }}
</style>""", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Cached data access. Streamlit Cloud has an ephemeral filesystem, so these
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
def get_timeline(persistent_id: str) -> list[dict]:
    """The outcome history for one case - the closest thing to a case file."""
    if not persistent_id:
        return []
    try:
        raw = police.outcomes_for_crime(persistent_id)
    except police.PoliceUKError:
        return []
    return [{"date": o.get("date", ""),
             "name": (o.get("category") or {}).get("name", "")}
            for o in (raw or {}).get("outcomes", [])]


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
# Dossier components
# --------------------------------------------------------------------------

def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def stamp(status: str) -> str:
    """A rubber-stamp status mark. Reads as records-office, not as evidence."""
    c = STATUS_COLOR[status]
    return (f'<span style="display:inline-block;border:2px solid {c};color:{c};'
            f'font-family:\'Courier New\',monospace;font-size:11px;font-weight:700;'
            f'letter-spacing:.14em;text-transform:uppercase;padding:4px 10px;'
            f'border-radius:3px;transform:rotate(-1.4deg);'
            f'opacity:.92">{_e(triage.STATUS_LABEL[status])}</span>')


def case_file(row: pd.Series, timeline: list[dict], breakdown: dict,
              nearby: int) -> str:
    """One case rendered as a dossier page."""
    cat = glossary.describe_category(row["category"])
    ref = (row["persistent_id"] or "")[:16].upper() or "NO REFERENCE ISSUED"

    events = "".join(
        f'<tr><td style="padding:5px 14px 5px 0;font-family:\'Courier New\',monospace;'
        f'font-size:12px;color:{INK_DIM};white-space:nowrap;vertical-align:top">{_e(ev["date"])}</td>'
        f'<td style="padding:5px 0"><b>{_e(ev["name"])}</b><br>'
        f'<span style="color:{INK_DIM};font-size:12.5px">'
        f'{_e(glossary.describe_outcome(ev["name"]))}</span></td></tr>'
        for ev in timeline) or (
        f'<tr><td colspan="2" style="color:{INK_DIM};font-size:12.5px;padding:5px 0">'
        f'{_e(glossary.describe_outcome(row["outcome"]))}</td></tr>')

    bars = "".join(
        f'<div style="margin:5px 0"><div style="display:flex;justify-content:space-between;'
        f'font-size:11.5px"><span>{_e(t["label"])}</span>'
        f'<span style="font-family:\'Courier New\',monospace">{t["weight"]:.2f}</span></div>'
        f'<div style="height:5px;background:{RULE};border-radius:3px;overflow:hidden">'
        f'<div style="height:100%;width:{min(t["weight"],1)*100:.0f}%;background:{INK}"></div></div>'
        f'<div style="font-size:11px;color:{INK_DIM};margin-top:2px">{_e(t["note"])}</div></div>'
        for t in breakdown["terms"])

    caveat = (f'<div style="margin-top:7px;padding:8px 10px;background:#efe4cb;'
              f'border-left:3px solid #b45309;font-size:12.5px">'
              f'<b>Read with care.</b> {_e(cat["caveat"])}</div>') if cat["caveat"] else ""

    return f"""
<div style="background:{CARD};border:1px solid {RULE};border-left:5px solid {INK};
     border-radius:3px;padding:20px 24px;font-family:-apple-system,'Segoe UI',sans-serif;
     color:{INK};box-shadow:0 1px 3px rgba(0,0,0,.07)">

  <div style="display:flex;justify-content:space-between;align-items:flex-start;
       gap:16px;flex-wrap:wrap">
    <div>
      <div style="font-family:'Courier New',monospace;font-size:10.5px;
           letter-spacing:.2em;color:{INK_DIM}">CASE REFERENCE</div>
      <div style="font-family:'Courier New',monospace;font-size:15px;
           letter-spacing:.06em">{_e(ref)}</div>
    </div>
    <div>{stamp(row["status"])}</div>
  </div>

  <hr style="border:none;border-top:1px solid {RULE};margin:16px 0">

  <div style="font-family:'Courier New',monospace;font-size:10.5px;
       letter-spacing:.2em;color:{INK_DIM}">RECORDED OFFENCE</div>
  <div style="font-family:Georgia,serif;font-size:20px;margin:2px 0 5px">{_e(cat["name"])}</div>
  <div style="font-size:13.5px">{_e(cat["covers"])}</div>
  {caveat}

  <hr style="border:none;border-top:1px solid {RULE};margin:16px 0">

  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:16px">
    <div>
      <div style="font-family:'Courier New',monospace;font-size:10.5px;
           letter-spacing:.2em;color:{INK_DIM}">LOCATION</div>
      <div style="font-size:15px;margin:2px 0 4px"><b>{_e(row["street"])}</b></div>
      <div style="font-size:11.5px;color:{INK_DIM}">{_e(glossary.describe_location(row["street"]))}</div>
    </div>
    <div>
      <div style="font-family:'Courier New',monospace;font-size:10.5px;
           letter-spacing:.2em;color:{INK_DIM}">RECORDED</div>
      <div style="font-size:15px;margin:2px 0 4px"><b>{_e(row["month"])}</b></div>
      <div style="font-size:11.5px;color:{INK_DIM}">{int(row["age_months"])} months before the
        latest published month</div>
    </div>
    <div>
      <div style="font-family:'Courier New',monospace;font-size:10.5px;
           letter-spacing:.2em;color:{INK_DIM}">IMMEDIATE AREA</div>
      <div style="font-size:15px;margin:2px 0 4px"><b>{nearby}</b> other unsolved</div>
      <div style="font-size:11.5px;color:{INK_DIM}">same category, within 300 m,
        within two months either side</div>
    </div>
  </div>

  <hr style="border:none;border-top:1px solid {RULE};margin:16px 0">

  <div style="font-family:'Courier New',monospace;font-size:10.5px;
       letter-spacing:.2em;color:{INK_DIM};margin-bottom:7px">INVESTIGATION HISTORY</div>
  <table style="width:100%;border-collapse:collapse;font-size:13.5px">{events}</table>

  <hr style="border:none;border-top:1px solid {RULE};margin:16px 0">

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:22px">
    <div>
      <div style="font-family:'Courier New',monospace;font-size:10.5px;
           letter-spacing:.2em;color:{INK_DIM};margin-bottom:5px">WHY IT RANKS HERE</div>
      {bars}
    </div>
    <div>
      <div style="font-family:'Courier New',monospace;font-size:10.5px;
           letter-spacing:.2em;color:{INK_DIM}">PRIORITY</div>
      <div style="font-family:Georgia,serif;font-size:40px;line-height:1.1">
        {breakdown["priority"]}</div>
      <div style="font-size:11.5px;color:{INK_DIM};margin-top:5px">
        Case score {breakdown["case_score"]} · local excess
        {breakdown["place_score"]} (z={breakdown["hotspot_z"]})<br>
        <span style="font-family:'Courier New',monospace;font-size:10.5px">
          {_e(breakdown["formula"])}</span></div>
      <div style="font-size:11.5px;color:{INK_DIM};margin-top:9px">
        A ranking heuristic set in <code>atlas/triage.py</code>, not an official
        assessment. It says where to look, never what happened.</div>
    </div>
  </div>

  <div style="margin-top:16px;padding-top:11px;border-top:1px dashed {RULE};
       font-size:11.5px;color:{INK_DIM}">
    <b>There is no narrative on this record.</b> police.uk publishes a context
    field for every crime and leaves it empty - it was blank in all 8,026
    records of the sample this was built against. Everything above is derived
    from the category, the location point and the outcome history. Nothing here
    describes what actually happened, and nothing here names anyone.
  </div>
</div>"""


def render_map(plot: pd.DataFrame, hotspots: list[dict],
               lat: float, lng: float, height: int = 500) -> None:
    """
    Leaflet with plain OSM tiles. st.map draws no basemap without a Mapbox
    token, and pydeck's TileLayer cannot be expressed through its JSON
    converter, so neither built-in option ever requests a tile.
    """
    points = [{"lat": r.lat, "lng": r.lng, "c": STATUS_COLOR[r.status],
               "r": 3 + r.priority * 0.07,
               "t": f"{r.street} — {r.status_label} — {r.month} · priority {r.priority}"}
              for r in plot.itertuples()]
    rings = [{"lat": h["centre"]["lat"], "lng": h["centre"]["lng"],
              "rad": max(h["cell_m"] * 0.75, 90),
              "t": (f"{h['ratio']}x the surrounding area — {h['observed']} unsolved "
                    f"where {h['expected']} expected (z={h['excess_z']})")}
             for h in hotspots]

    html_doc = f"""
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<div id="m" style="height:{height - 10}px;width:100%;border:1px solid {RULE};
     border-radius:3px;overflow:hidden"></div>
<script>
// scrollWheelZoom off so the page still scrolls when the cursor crosses the
// map; click once to zoom with the wheel, as embedded maps conventionally do.
const map = L.map('m', {{scrollWheelZoom:false}}).setView([{lat}, {lng}], 13);
map.on('click', () => map.scrollWheelZoom.enable());
map.on('mouseout', () => map.scrollWheelZoom.disable());
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution:'&copy; OpenStreetMap contributors', maxZoom:19, opacity:0.72
}}).addTo(map);
{json.dumps(rings)}.forEach(h => L.circle([h.lat,h.lng], {{
  radius:h.rad, color:'#b45309', weight:1, fillColor:'#b45309', fillOpacity:.10
}}).addTo(map).bindPopup(h.t));
{json.dumps(points)}.forEach(p => L.circleMarker([p.lat,p.lng], {{
  radius:p.r, color:'#fdfcf8', weight:1, fillColor:p.c, fillOpacity:.85
}}).addTo(map).bindPopup(p.t));
// Streamlit sizes the component iframe after the script runs, so Leaflet
// measures the wrong width and tiles cover only part of it.
setTimeout(() => map.invalidateSize(), 150);
setTimeout(() => map.invalidateSize(), 600);
window.addEventListener('resize', () => map.invalidateSize());
</script>"""
    components.html(html_doc, height=height)


def nearby_count(cases: pd.DataFrame, row: pd.Series, radius_m: float = 300,
                 window: int = 2) -> int:
    """Other unsolved cases of the same category close in space and time."""
    if pd.isna(row["lat"]):
        return 0
    same = cases[(cases["category"] == row["category"])
                 & (cases["status"].isin([triage.COLD, triage.STALLED, triage.UNKNOWN]))
                 & (cases["persistent_id"] != row["persistent_id"])
                 & cases["lat"].notna()]
    if same.empty:
        return 0
    near = same[same.apply(
        lambda r: abs(triage.months_between(r["month"], row["month"])) <= window
        and triage.haversine_m(row["lat"], row["lng"], r["lat"], r["lng"]) <= radius_m,
        axis=1)]
    return int(len(near))


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------

st.sidebar.markdown(
    f"<div style='font-family:\"Courier New\",monospace;font-size:10.5px;"
    f"letter-spacing:.2em;color:{INK_DIM}'>RECORDS OFFICE</div>"
    f"<div style='font-family:Georgia,serif;font-size:23px;margin:1px 0 3px'>"
    f"Cold Case Atlas</div>"
    f"<div style='font-size:11.5px;color:{INK_DIM}'>Unsolved-case intelligence "
    f"from official open data</div><hr style='border:none;border-top:1px solid "
    f"{RULE};margin:13px 0'>", unsafe_allow_html=True)

postcode = st.sidebar.text_input("Postcode", value="L1 8JQ",
                                 help="England, Wales or Northern Ireland")
category_label = st.sidebar.selectbox("Offence category", list(CATEGORIES))
months = st.sidebar.select_slider("Months to sweep", [6, 12, 18, 24, 36, 48, 60],
                                  value=24)
st.sidebar.markdown(f"<hr style='border:none;border-top:1px solid {RULE}'>",
                    unsafe_allow_html=True)
st.sidebar.caption("Contains public sector information licensed under the Open "
                   "Government Licence v3.0. Source: data.police.uk. "
                   "Statistics from Eurostat.")

# --------------------------------------------------------------------------
# Load
# --------------------------------------------------------------------------

try:
    latest = get_latest_month()
    lat, lng, place = geocode(postcode)
except police.PoliceUKError as e:
    st.error(f"Could not look that up: {e}")
    st.stop()

with st.spinner(f"Pulling {months} months of records for {place}…"):
    try:
        port = get_portfolio(lat, lng, CATEGORIES[category_label], months, latest)
    except police.PoliceUKError as e:
        st.error(f"data.police.uk is not responding: {e}")
        st.stop()

cases = pd.DataFrame(port["cases"])
tally = port["tally"]

st.markdown(
    f"<div class='dossier-head'>DISTRICT FILE · {_e(place)}</div>"
    f"<div class='dossier-sub'>{_e(category_label)}</div>"
    f"<div style='font-family:\"Courier New\",monospace;font-size:11.5px;"
    f"color:{INK_DIM}'>{port['months_swept']} months to {port['latest_month']} · "
    f"records within roughly one mile</div>", unsafe_allow_html=True)
st.write("")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("On file", f"{port['total']:,}")
c2.metric("Cold · no suspect", f"{tally.get(triage.COLD, 0):,}")
c3.metric("Stalled", f"{tally.get(triage.STALLED, 0):,}")
c4.metric("Unsolved", f"{port['unsolved_pct']}%")
c5.metric("Hotspots", len(port["hotspots"]))

if cases.empty:
    st.info("No records returned for that combination.")
    st.stop()

tab_files, tab_map, tab_patterns, tab_eu, tab_method = st.tabs(
    ["Case files", "Map", "Patterns", "Europe", "Method"])


# --------------------------------------------------------------------------
# Case files
# --------------------------------------------------------------------------

with tab_files:
    st.caption("Select a row to open its file.")
    listing = cases[cases["priority"] > 0].head(300).reset_index(drop=True)
    view = pd.DataFrame({
        "Priority": listing["priority"],
        "Status": listing["status_label"],
        "Offence": listing["category"].map(
            lambda c: glossary.describe_category(c)["name"]),
        "Location": listing["street"],
        "Recorded": listing["month"],
        "Age (months)": listing["age_months"],
    })
    event = st.dataframe(
        view, width="stretch", hide_index=True, height=330,
        on_select="rerun", selection_mode="single-row", key="case_table",
        column_config={"Priority": st.column_config.ProgressColumn(
            "Priority", min_value=0, max_value=100, format="%.1f")})

    picked = event.selection.rows[0] if event.selection.rows else 0
    row = listing.iloc[picked]
    st.markdown(
        case_file(row, get_timeline(row["persistent_id"]),
                  triage.explain(
                      police.Crime(id=int(row["id"]), persistent_id=row["persistent_id"],
                                   category=row["category"], month=row["month"],
                                   lat=row["lat"], lng=row["lng"], street=row["street"],
                                   outcome=row["outcome"], context=""),
                      latest, float(row["hotspot_z"])),
                  nearby_count(cases, row)),
        unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Map
# --------------------------------------------------------------------------

with tab_map:
    unsolved = cases[(cases["status"] != triage.RESOLVED) & cases["lat"].notna()]
    MAP_CAP = 400
    plot = unsolved.nlargest(MAP_CAP, "priority").copy()
    if plot.empty:
        st.info("Nothing mappable in this selection.")
    else:
        render_map(plot, port["hotspots"], lat, lng)
        chips = " ".join(
            f'<span style="display:inline-block;margin-right:14px;font-size:12px">'
            f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;'
            f'background:{STATUS_COLOR[s]};margin-right:5px"></span>'
            f'{_e(triage.STATUS_LABEL[s])}</span>'
            for s in (triage.COLD, triage.STALLED, triage.UNKNOWN, triage.OPEN)
            if s in set(plot["status"]))
        st.markdown(chips + f'<span style="font-size:12px;color:{INK_DIM}">'
                            f'· marker size follows priority · amber rings are hotspots</span>',
                    unsafe_allow_html=True)
        if len(unsolved) > MAP_CAP:
            st.caption(f"Showing the {MAP_CAP} highest-priority of "
                       f"{len(unsolved):,} unsolved records.")


# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------

with tab_patterns:
    if not port["hotspots"]:
        st.info("No cell in this area carries a statistically unusual "
                "concentration of unsolved crime.")
    else:
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
        st.markdown(
            f"<div style='background:{CARD};border-left:3px solid #b45309;"
            f"padding:11px 14px;font-size:13px;margin-top:9px'>"
            f"Each 200 m cell is compared with the ring of cells around it, so "
            f"this is excess over the <b>local</b> baseline rather than raw "
            f"density — otherwise every city centre lights up. It is still a "
            f"pattern in <i>reported</i> data, not evidence that offences are "
            f"linked, and police.uk snaps each record to an anonymised map "
            f"point, so a cell can look busy because a snap point sits inside "
            f"it. <b>Treat a hotspot as a question, never a finding.</b></div>",
            unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Europe
# --------------------------------------------------------------------------

with tab_eu:
    st.markdown("#### Police-recorded offences across Europe")
    st.caption("Eurostat crim_off_cat — 41 countries, ICCS categories")
    try:
        years = get_years()
    except es.EurostatError as e:
        st.error(f"Eurostat is not responding: {e}")
        st.stop()

    col_a, col_b = st.columns([1, 2])
    year = col_a.selectbox("Year", list(reversed(years)), index=2)
    offence_label = col_b.selectbox("Offence", list(es.SERIOUS.values()))
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
        # One series, one hue: magnitude, not identity. No legend - the caption
        # names the measure - and values sit in text ink beside each bar.
        base = alt.Chart(top).encode(
            y=alt.Y("country:N", sort="-x", title=None,
                    axis=alt.Axis(labelColor=INK, labelFontSize=11)),
            x=alt.X("offences:Q", title=None,
                    axis=alt.Axis(grid=True, gridColor=RULE, format="~s",
                                  labelColor=INK_DIM)))
        chart = base.mark_bar(cornerRadiusEnd=3, size=13, color="#1d4ed8").encode(
            tooltip=[alt.Tooltip("country:N", title="Country"),
                     alt.Tooltip("offences:Q", title=offence_label, format=",.0f")])
        labels = base.mark_text(align="left", dx=6, fontSize=11, color=INK_DIM).encode(
            text=alt.Text("offences:Q", format=",.0f"))
        st.altair_chart((chart + labels).properties(height=max(320, 22 * len(top)))
                        .configure_view(strokeWidth=0)
                        .configure(background="transparent"), width="stretch")
        st.caption(f"**{offence_label}, {year}.** Raw counts, not "
                   "population-adjusted — comparing Germany with Malta says more "
                   "about population than crime. Recording practice also differs "
                   "by country, so these are national reporting figures rather "
                   "than like-for-like rates.")
        with st.expander("Table view"):
            st.dataframe(df.rename(columns={"country": "Country",
                                            "offences": offence_label}),
                         width="stretch", hide_index=True)


# --------------------------------------------------------------------------
# Method
# --------------------------------------------------------------------------

with tab_method:
    st.markdown(f"""
#### What a case file can and cannot tell you

`data.police.uk` publishes **no narrative for any crime**. Every record carries
a `context` field meant to describe the offence, and it was empty in all 8,026
records of the sample this was built against. So nothing in this app describes
what happened at a scene — a case file here is assembled from the offence
category, the snapped location point, and the outcome history, because that is
everything there is.

**Case status.** The outcome text is where the useful distinction hides:

| Status | Meaning |
|---|---|
| **Cold** | Investigation complete, **no suspect identified** — ran out of leads |
| **Stalled** | A suspect *was* identified and it still did not proceed |
| **Open** | Outcome pending |
| **No outcome published** | The force went silent — not the same as resolved |
| **Resolved** | Charged, cautioned, sentenced or otherwise disposed |

A record older than twelve months with no outcome is treated as cold rather
than pending, because forces run about two months behind.

**Hotspots** compare each 200 m cell with the ring of cells around it, scored
as a Poisson surprise. An earlier version clustered raw density and returned
1,040 crimes within 443 m in Liverpool — a nightlife district, not a pattern.

**Priority** is `0.75 × (severity × unsolved status × age) + 0.25 × local
excess`. Severity weights are a judgement call set in `atlas/triage.py`, not an
official ranking. Every file shows its own arithmetic.

#### What this app deliberately does not do

It carries no personal data. It names nobody, and hosts no community
speculation about who did what.

That is the constraint the whole design is built around, not squeamishness.
Aggregating forum accusations runs into UK/EU **GDPR Article 10** —
offence-related personal data may only be processed under official authority or
where authorised by law — and the **Defamation Act 2013**, where repeating
someone else's allegation makes you a publisher of it. Reddit's
misidentification of Sunil Tripathi after the Boston Marathon bombing, and the
professor wrongly named during the Idaho student murders, are what that costs an
innocent person.

The repository does contain a community layer for local use. It masks every
person name the police did not themselves publish, and drops any passage
reading as an accusation rather than redacting it — a redacted accusation is
still an accusation with a blank where the name goes.

#### Limits

Locations are snapped to anonymised street points, never addresses — the
biggest caveat on the map and the hotspots. Scotland is absent; Police Scotland
does not publish here. Eurostat counts are raw and not population-adjusted.

#### If you want to actually help

This app reads published records; it does not investigate.
[Locate International](https://locate.international) is a UK charity that
trains volunteers to run genuine cold case reviews on unidentified bodies and
long-term missing persons, alongside police. Information on a live case goes to
the force or Crimestoppers on 0800 555 111 — never a forum.
    """)
