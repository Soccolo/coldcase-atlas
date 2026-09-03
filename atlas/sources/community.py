"""
Reddit community layer.

Reddit blocks unauthenticated .json endpoints (403 as of now), so this uses
the official OAuth API. It uses the application-only `client_credentials`
grant, which means you register an app and never hand this tool a password.

Free tier is 100 queries/minute per client id. Reddit's terms require a
separate commercial agreement for commercial use - this is a personal
research tool, which is what the free tier is for. If this ever becomes a
product, that agreement comes first.

Data handling: results are held in an in-memory TTL cache and never written
to disk. Nothing from here is persisted, which keeps the storage-side GDPR
question simple - there is no store to be subject to a subject-access or
erasure request.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import requests

from ..safety import screen_many

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"

# Subreddits that discuss unsolved UK/EU cases. r/RBI is deliberately absent:
# it is an active investigation board where naming private individuals is the
# norm, which is exactly the content this app must not surface.
DEFAULT_SUBS = [
    "UnresolvedMysteries",
    "coldcases",
    "gratefuldoe",
    "UnsolvedMysteries",
    "MissingPersons",
    "TrueCrimeDiscussion",
]

UA = "windows:coldcase-atlas:0.1.0 (personal research tool)"


class RedditUnavailable(RuntimeError):
    """Raised when credentials are absent or Reddit refuses the request."""


@dataclass
class _Token:
    value: str
    expires_at: float


_token: _Token | None = None
_cache: dict[str, tuple[float, list[dict]]] = {}
CACHE_TTL = 900  # 15 minutes


def configured() -> bool:
    return bool(os.getenv("REDDIT_CLIENT_ID") and os.getenv("REDDIT_CLIENT_SECRET"))


def _get_token() -> str:
    global _token
    if _token and _token.expires_at > time.time() + 60:
        return _token.value

    cid = os.getenv("REDDIT_CLIENT_ID")
    secret = os.getenv("REDDIT_CLIENT_SECRET")
    if not (cid and secret):
        raise RedditUnavailable(
            "Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET. Create a 'script' "
            "app at https://www.reddit.com/prefs/apps - no password required."
        )

    r = requests.post(
        TOKEN_URL,
        auth=(cid, secret),
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": UA},
        timeout=20,
    )
    if r.status_code != 200:
        raise RedditUnavailable(f"Reddit auth failed ({r.status_code}): {r.text[:200]}")

    d = r.json()
    _token = _Token(d["access_token"], time.time() + d.get("expires_in", 3600))
    return _token.value


def _api_get(path: str, **params) -> dict:
    r = requests.get(
        f"{API}{path}",
        headers={"Authorization": f"Bearer {_get_token()}", "User-Agent": UA},
        params=params,
        timeout=25,
    )
    if r.status_code == 429:
        raise RedditUnavailable("Reddit rate limit hit - wait a minute.")
    if r.status_code != 200:
        raise RedditUnavailable(f"Reddit returned {r.status_code}")
    return r.json()


def search(query: str, *, subs: list[str] | None = None, limit: int = 15,
           official_names: set[str] | None = None) -> list[dict]:
    """
    Search the case subreddits and return SCREENED results only.

    Every item that comes back has passed atlas.safety.screen_many, so it
    carries provenance="community", verified=False, and contains no name the
    official record did not already publish.
    """
    subs = subs or DEFAULT_SUBS
    key = f"{query}|{','.join(subs)}|{limit}"
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]

    data = _api_get(
        f"/r/{'+'.join(subs)}/search",
        q=query, restrict_sr="true", sort="relevance", limit=limit, t="all",
    )

    raw = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        body = f"{d.get('title', '')}\n\n{d.get('selftext', '')}".strip()
        raw.append({
            "id": d.get("id"),
            "subreddit": d.get("subreddit"),
            "score": d.get("score", 0),
            "num_comments": d.get("num_comments", 0),
            "created_utc": d.get("created_utc", 0),
            "permalink": f"https://reddit.com{d.get('permalink', '')}",
            "body": body,
        })

    screened = screen_many(raw, official_names=official_names)
    _cache[key] = (time.time() + CACHE_TTL, screened)
    return screened


def context_for_case(street: str, area: str, category: str,
                     year: str | None = None) -> list[dict]:
    """
    Look for community discussion that might relate to a case.

    Deliberately searches on PLACE and TIME, never on a person. A location
    query cannot pull back a thread simply because it names a suspect.
    """
    terms = [t for t in (street, area) if t and t != "location withheld"]
    if not terms:
        return []
    q = " OR ".join(f'"{t}"' for t in terms)
    if year:
        q = f"({q}) {year}"
    try:
        return search(q, limit=10)
    except RedditUnavailable:
        return []
