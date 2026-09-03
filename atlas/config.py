"""
Feature gates.

The public Streamlit deployment serves police.uk and Eurostat only. Both are
explicitly licensed for public re-use - Open Government Licence v3.0 and the
Eurostat reuse policy - and neither carries personal data.

The Interpol layer stays local-only, for two reasons. It displays named
individuals, which would make whoever deploys it a data controller for a public
page. And it reaches its API by impersonating a browser's TLS fingerprint to
get past Akamai bot mitigation, which is a defensible thing for one person to
do at low volume against data published for public view, and not a defensible
thing to expose to open internet traffic from a shared datacentre IP.

So it is off unless explicitly switched on:

    ATLAS_ENABLE_INTERPOL=1 python run.py

Defence in depth: `curl_cffi` is not in the deployment requirements either, so
the cloud build cannot make the call even if the flag were set.
"""
from __future__ import annotations

import os


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def interpol_enabled() -> bool:
    """Interpol layer. Off by default; local opt-in only."""
    return _flag("ATLAS_ENABLE_INTERPOL", False)


def reddit_enabled() -> bool:
    """Reddit layer. Needs credentials as well as this flag."""
    return _flag("ATLAS_ENABLE_REDDIT", True)
