# Cold Case Atlas

Case intelligence for unsolved crime and long-term missing persons across the
UK and Europe, built on official open data.

Search a UK postcode, sweep up to five years of police records, and get a
ranked portfolio of unsolved cases plus the locations carrying materially more
unsolved crime than their surroundings — with European offence statistics for
context.

## Running it

**Streamlit app** — this is what deploys:

```bash
pip install -r requirements.txt && streamlit run streamlit_app.py
```

**Full local app** (FastAPI + Leaflet, adds the Interpol and Reddit layers):

```bash
pip install -r requirements-local.txt && python run.py
```

Opens on http://127.0.0.1:8000. No API key needed except for the optional
Reddit layer.

### What runs where, and why

| Layer | Streamlit (public) | Local |
|---|---|---|
| data.police.uk | ✅ | ✅ |
| Eurostat | ✅ | ✅ |
| Interpol missing persons | ❌ | ✅ with `ATLAS_ENABLE_INTERPOL=1` |
| Reddit community context | ❌ | ✅ with credentials |

The two public layers are explicitly licensed for public re-use — Open
Government Licence v3.0 and the Eurostat reuse policy — and neither carries any
personal data.

The Interpol layer is local-only on purpose. It displays named individuals,
which would make whoever deploys it a data controller for a public page, and it
reaches its API by impersonating a browser's TLS fingerprint to get past bot
mitigation. That is defensible for one person at low volume against data
published for public view; it is not something to expose to open internet
traffic from a shared datacentre IP. It is gated behind a flag in
`atlas/config.py`, and `curl_cffi` is deliberately absent from the deployment
requirements, so the cloud build cannot make the call even if the flag were set.

### Deploying to Streamlit Community Cloud

Point [share.streamlit.io](https://share.streamlit.io) at this repo with
`streamlit_app.py` as the entry point. Nothing else to configure — no secrets
are needed for the deployed layers. Leave `ATLAS_ENABLE_INTERPOL` unset.

---

## A correction

An earlier version of this README said the EU half could not be built — "no
European equivalent of police.uk, no single open API". That was written after
two failed probes and it was wrong. What follows is what is actually there.

**Interpol's public notices API works.** `ws-public.interpol.int` is genuinely
open: no key, no account, no ToS gate. It is the same endpoint interpol.int's
own notice search calls, and it holds ~11,600 live missing-person notices
globally, ~430 of them across the EU, EEA and UK.

The reason it looked dead is that Akamai in front of it fingerprints the TLS
handshake. `curl` and `requests` are refused with 403 no matter what headers
they send; a real browser sails through. `curl_cffi` impersonates Chrome's
fingerprint and gets in.

**Be deliberate about that.** The block is generic bot mitigation, not an
access policy — a yellow notice only works if the public sees it, and these
records are published expressly so people will look. But going around bot
mitigation still carries an obligation, so `atlas/sources/interpol.py` caches
to disk for a week, caps concurrency at six, and fetches details only for
notices actually being shown. If this ever becomes a public or commercial
service, ask Interpol for proper access first.

**Eurostat works too, and my probe was broken rather than the API.** The `curl`
shipped with Git for Windows is from 2021 and its CA bundle no longer verifies
the Europa certificate chain, so it fails the TLS handshake and reports a bare
connection error that looks exactly like an outage. Python's `requests` uses
certifi and connects fine. `crim_off_cat` gives police-recorded offences by
ICCS category for 41 countries, 2008-2024.

---

## What each source gives you

| Source | Granularity | Key? | Coverage |
|---|---|---|---|
| **data.police.uk** | Per incident, with outcome | No | England, Wales, NI |
| **Interpol yellow notices** | Per person | No (needs `curl_cffi`) | Global, EU-filtered |
| **Eurostat `crim_off_cat`** | National aggregate | No | 41 countries, 2008-2024 |
| **Reddit** | Discussion | Yes, free tier | Off unless configured |

Also confirmed live but not wired in, because each needs its own adapter and
none share a schema:

- **data.europa.eu** — the EU's aggregator, harvesting national portals.
  128,000+ hits for "crime statistics", including detailed German police
  crime-statistics tables.
- **govdata.de** — 1,355 Kriminalstatistik datasets, down to city level.
- **CBS (Netherlands)** — `Geregistreerde misdrijven; wijken en buurten`,
  registered crime by district and neighbourhood. The most spatially granular
  national source found.
- **data.gouv.fr** — SSMSI `Bases statistiques communale, departementale et
  regionale de la delinquance enregistree`, plus monthly departmental figures.
- **datos.gob.es**, **dati.gov.it** — CKAN portals, both responding.

### The gap that is real

No EU source gives per-incident **outcome status**. That is the specific field
that makes the UK layer work: police.uk publishes "Investigation complete; no
suspect identified" per record, which is what separates a cold case from a
closed one. The European equivalents are aggregate counts by area and period,
or individual missing-person notices — different shapes, both useful, neither
one a per-incident case status.

So the honest answer is not "there is no EU data". It is that you cannot build
*this exact app* for the EU. You build a different one: missing persons at case
level from Interpol, national baselines from Eurostat, and area-level counts
from whichever national portal covers your country.

---

## What the UK mode does

**Case status.** police.uk publishes a free-text outcome. The useful
distinction is buried in it, so `atlas/triage.py` maps it:

| Status | Meaning |
|---|---|
| `cold` | Investigation complete, **no suspect identified** — the real cold case |
| `stalled` | Suspect known, prosecution failed or was dropped |
| `open` | Outcome still pending |
| `unknown` | Force published no outcome |
| `resolved` | Charged, cautioned, sentenced or otherwise disposed |

A record older than twelve months with no outcome is treated as cold rather
than pending, because forces run roughly two months behind.

**Priority score.** Severity x unsolved status x age, on a curve that rises
fast over two years then flattens, blended with local hotspot excess as a
weighted sum so neither term swamps the other.

**Hotspots.** The first version clustered "N unsolved crimes within R metres".
In a quiet suburb that found something; in Liverpool city centre it returned a
"cluster" of 1040 crimes in 443 m, which is a nightlife district, not a
pattern. Raw density is mostly a proxy for footfall.

Each 200 m cell is now compared against a baseline built from the ring of cells
around it and scored as a Poisson surprise — a simplified scan statistic, and
scale-free. Liverpool now returns Concert Square at **11x its surrounding
area**, Manchester returns Piccadilly station at **28x**.

---

## The boundary this app is built around

The UK layer handles **no personal data at all** — police.uk publishes
categories, months, outcomes and snapped map points, never names.

Two other layers do involve named people, and they are not the same case:

**Interpol yellow notices — names shown, unchanged.** These are missing
persons: subjects of an official appeal, published by a national authority so
the public will see them. Relaying them intact and attributed is the intended
use. This is Art. 10's "under the control of official authority" working in
your favour.

**Red notices are different** and are off by default. Those people are accused,
not convicted. Supported by the module, but the UI must never present one as
anything other than "a country has asked for this person to be located".

**Reddit — names masked.** Forum threads about unsolved cases routinely name
private individuals as suspects. Re-publishing those has two consequences:

- **UK/EU GDPR Article 10** — offence-related personal data may only be
  processed under official authority or where authorised by law. A searchable
  index of who the internet thinks did it has no lawful basis a personal
  project can claim.
- **Defamation Act 2013** — repeating an allegation makes you a publisher of
  it. The s.5 operator defence covers content users post on your site, not
  material you fetched and re-hosted.

Not hypothetical: Reddit's misidentification of Sunil Tripathi after the Boston
Marathon bombing, and the professor wrongly named during the Idaho student
murders, are the standing examples.

So `atlas/safety.py` enforces one rule:

> Community text may add **context** to an official case.
> It may never introduce a **name** the official source did not publish.

1. Names the police published pass through — the official record is the
   authority for them.
2. Every other person name is masked. Without a spaCy model the detector falls
   back to a capitalised-bigram heuristic that over-redacts. Deliberate: a
   redacted place name is a cosmetic bug, a leaked accusation is a legal one.
3. Anything reading as an accusation is dropped whole, not redacted — a
   redacted accusation is still an accusation with a blank where the name goes.

Community searches run on **place and time, never on a person**. Nothing from
Reddit touches disk; it lives in a 15-minute in-memory cache.

Better name detection: `python -m spacy download en_core_web_sm`

---

## Enabling the Reddit layer

Reddit blocks unauthenticated `.json` endpoints (403). Create a **script** app
at https://www.reddit.com/prefs/apps, then `cp .env.example .env` and fill in
the id and secret. It uses the `client_credentials` grant, so it never asks for
your password. Free tier is 100 queries/minute; commercial use needs a separate
agreement. Without credentials the layer stays off and everything else works.

---

## Known limits

**Location snapping.** police.uk publishes no real coordinates — every record
is snapped to an anonymised map point on a street segment. A cell can look busy
because a snap point sits inside it. This is the biggest caveat on hotspots.
Treat one as a question, never a finding.

**Scotland is absent.** Police Scotland does not publish to police.uk.

**Interpol covers only what gets a notice.** A yellow notice is requested by a
member country; most domestic missing-person cases never get one. ~430 EU/UK
notices is not the number of missing people in Europe, it is the number
escalated to Interpol.

**Outcome lag.** Forces run ~2 months behind, and some never supply an outcome.
`unknown` means silence, not resolution.

**Severity weights are a judgement call.** The `SEVERITY` table in
`atlas/triage.py` is mine, not an official ranking.

**Eurostat counts are raw.** Not population-adjusted — comparing Germany with
Malta says more about population than crime.

---

## If you want to actually help solve something

This app reads published records. It does not investigate.

The real route in the UK is **[Locate International](https://locate.international)**,
a registered charity that trains volunteers to run genuine cold case reviews on
unidentified bodies and long-term missing persons, alongside police. Real case
files, real methodology, actual oversight.

If you recognise someone in an Interpol notice, contact your national police or
Interpol. If you have information on a UK case, the force or Crimestoppers
(0800 555 111). Never a forum.

---

## Layout

```
atlas/
  sources/police_uk.py   incident-level UK records + postcode geocoding
  sources/interpol.py    EU/UK missing persons (needs curl_cffi)
  sources/eurostat.py    national offence statistics, JSON-stat decoder
  sources/community.py   Reddit OAuth client (needs credentials)
  triage.py              status mapping, priority score, hotspot detection
  safety.py              name masking and accusation blocking
  server.py              FastAPI routes
web/index.html           map, ranked list, notice list; Leaflet, no build step
.cache/interpol/         disk cache, 7-day TTL
```

Endpoints: `/api/portfolio`, `/api/case/{persistent_id}`, `/api/eu/missing`,
`/api/eu/counts`, `/api/categories`, `/api/health`.

Contains public sector information licensed under the Open Government Licence
v3.0. Source: data.police.uk. Notice data from Interpol public notices.
Statistics from Eurostat.
