"""
Guardrails for community (Reddit) content.

Why this module exists
----------------------
The official layer of this app handles no personal data at all. The moment
community discussion is pulled in, that changes: forum threads about unsolved
cases routinely name private individuals as suspects. Re-publishing those names
in an app has two specific consequences in the UK and EU:

  * UK/EU GDPR Article 10 - personal data relating to criminal offences may
    only be processed under the control of official authority or where
    authorised by law. Building a searchable index of "who the internet thinks
    did it" is squarely inside Article 10 and outside any lawful basis a
    hobby project can claim.

  * Defamation Act 2013 - repeating someone else's allegation makes you a
    publisher of it. The s.5 operator defence covers content posted by users
    on your own site; it does not cover material you fetched and re-hosted.

This has caused real harm, not hypothetical harm: Reddit's misidentification
of Sunil Tripathi after the Boston Marathon bombing, and the professor wrongly
named by online sleuths in the Idaho student murders, are the standing examples.

So the rule enforced here is narrow and mechanical:

    Community text may add CONTEXT to an official case.
    It may never introduce a NAME that the official source did not publish.

Names the police themselves published (a missing person, a named appeal
subject) pass through, because the official record is the authority for them.
Everything else is withheld, and any passage that reads as an accusation is
dropped whole rather than redacted - a redacted accusation is still an
accusation with a blank where the name goes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------
# Name detection
# --------------------------------------------------------------------------
# spaCy does this properly. Without a model installed we fall back to a
# capitalised-bigram heuristic, which over-redacts rather than under-redacts.
# That asymmetry is deliberate: a redacted place name is a cosmetic bug, a
# leaked accusation is a legal one.

_NLP = None
_NLP_TRIED = False


def _nlp():
    global _NLP, _NLP_TRIED
    if not _NLP_TRIED:
        _NLP_TRIED = True
        try:
            import spacy
            _NLP = spacy.load("en_core_web_sm")
        except Exception:
            _NLP = None
    return _NLP


# Words that look like names to the heuristic but are not people.
_NOT_PEOPLE = {
    "United Kingdom", "Great Britain", "Northern Ireland", "New Scotland",
    "Police Scotland", "Greater Manchester", "West Midlands", "North Yorkshire",
    "Crown Court", "Magistrates Court", "High Court", "Home Office",
    "Crimestoppers", "Missing Persons", "Cold Case", "True Crime",
    "Google Maps", "Street View", "Wayback Machine", "Freedom Of Information",
}

_NAME_RE = re.compile(
    r"\b([A-Z][a-z]{1,15})\s+((?:[A-Z][a-z]{1,15}|[A-Z]\.)\s+)?([A-Z][a-z]{1,15})\b"
)


def detect_person_names(text: str) -> set[str]:
    """Best-effort set of person names appearing in `text`."""
    nlp = _nlp()
    if nlp is not None:
        doc = nlp(text)
        return {e.text.strip() for e in doc.ents
                if e.label_ == "PERSON" and len(e.text.strip()) > 3}

    found = set()
    for m in _NAME_RE.finditer(text):
        cand = re.sub(r"\s+", " ", m.group(0)).strip()
        if cand in _NOT_PEOPLE:
            continue
        # Skip sentence-initial false positives like "The Police Officer"
        if cand.split()[0] in {"The", "This", "That", "These", "Those", "It"}:
            continue
        found.add(cand)
    return found


# --------------------------------------------------------------------------
# Accusation detection
# --------------------------------------------------------------------------
# If a passage attributes wrongdoing to a person, redaction is not enough -
# the passage is dropped. These patterns are intentionally broad.

_ACCUSATION_PATTERNS = [
    r"\b(did|done) it\b",
    r"\bis (the|a) (killer|murderer|perp|perpetrator|suspect|one)\b",
    r"\b(killed|murdered|abducted|attacked|raped|abused|stalked)\s+(her|him|them|the)\b",
    r"\b(guilty|responsible|behind (it|this))\b",
    r"\bit was (him|her|them)\b",
    r"\b(he|she|they) (definitely|obviously|clearly|100%) (did|knows)\b",
    r"\bmy (money|bet) is on\b",
    r"\bprime suspect\b",
    r"\bhas to be\s+[A-Z]",
    r"\b(covering|covered) (it )?up\b",
    r"\bknows more than\b",
    r"\b(inside|involved in) (job|the murder|the killing)\b",
]
_ACCUSATION_RE = re.compile("|".join(_ACCUSATION_PATTERNS), re.IGNORECASE)


def reads_as_accusation(text: str) -> bool:
    return bool(_ACCUSATION_RE.search(text))


# --------------------------------------------------------------------------
# The filter
# --------------------------------------------------------------------------

WITHHELD = "[name withheld]"


@dataclass
class Screened:
    """Result of screening one piece of community text."""
    text: str                    # safe to display (may be "")
    blocked: bool                # dropped entirely
    reason: str                  # why, for the audit trail
    names_withheld: int          # how many names were masked

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "blocked": self.blocked,
            "reason": self.reason,
            "names_withheld": self.names_withheld,
        }


def screen(text: str, *, official_names: set[str] | None = None) -> Screened:
    """
    Screen one comment or post body for display.

    `official_names` are names the police or an official appeal already
    published for this case - those are allowed through verbatim.
    """
    official = {n.lower() for n in (official_names or set())}
    text = (text or "").strip()

    if not text:
        return Screened("", True, "empty", 0)

    if reads_as_accusation(text):
        return Screened("", True, "reads as an accusation against a person", 0)

    names = {n for n in detect_person_names(text) if n.lower() not in official}
    # Also allow a first-name-only match against an official full name.
    names = {n for n in names
             if not any(n.lower() in o.split() for o in official)}

    if not names:
        return Screened(text, False, "clean", 0)

    redacted = text
    for n in sorted(names, key=len, reverse=True):
        redacted = re.sub(rf"\b{re.escape(n)}\b", WITHHELD, redacted)

    return Screened(redacted, False, "names masked", len(names))


def screen_many(items: list[dict], *, body_key: str = "body",
                official_names: set[str] | None = None) -> list[dict]:
    """
    Screen a list of community items, dropping blocked ones.

    Returns only what is safe to show, each carrying its provenance so the UI
    can never render it as though it were an official record.
    """
    out = []
    for it in items:
        res = screen(it.get(body_key, ""), official_names=official_names)
        if res.blocked:
            continue
        out.append({
            **{k: v for k, v in it.items() if k != body_key},
            "text": res.text,
            "names_withheld": res.names_withheld,
            "provenance": "community",
            "verified": False,
        })
    return out
