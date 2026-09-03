"""
Plain-English meaning for the codes police.uk publishes.

This module exists because of a hard limit in the data. Every record carries a
`context` field that is meant to describe the offence - and across an 8,026
record sample it was empty 8,026 times. Forces never populate it. There is no
narrative anywhere in the feed: no description, no summary, no "what happened".

So the only things that say what a case is *about* are its Home Office category
and its outcome history, and both are published as terse codes. Expanding them
honestly is the most that can be said - and saying what a category hides is
part of that. "Violence and sexual offences" covers a common assault and a rape
identically, which is the single most misleading thing on the page if left
unexplained.
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# Offence categories
# --------------------------------------------------------------------------
# `covers` is what the Home Office counting rules fold into the category.
# `caveat` is what a reader would otherwise get wrong.

CATEGORY = {
    "violent-crime": {
        "name": "Violence and sexual offences",
        "covers": "Homicide, assault with and without injury, harassment, "
                  "stalking, and all sexual offences.",
        "caveat": "The broadest category by far. A common assault and a rape "
                  "appear here identically - the published record cannot tell "
                  "you which you are looking at.",
    },
    "burglary": {
        "name": "Burglary",
        "covers": "Entering a building as a trespasser to steal, or attempting "
                  "to. Residential and business premises both count.",
        "caveat": "Since 2017 police.uk stopped splitting residential from "
                  "business, so a house and a warehouse look the same here.",
    },
    "robbery": {
        "name": "Robbery",
        "covers": "Theft using force, or the threat of force, against a person.",
        "caveat": "The force is what separates robbery from theft - it is a "
                  "violent offence, not a property one.",
    },
    "vehicle-crime": {
        "name": "Vehicle crime",
        "covers": "Theft of a vehicle, theft from a vehicle, and interference "
                  "with a vehicle.",
        "caveat": "Overwhelmingly theft *from* vehicles rather than of them.",
    },
    "criminal-damage-arson": {
        "name": "Criminal damage and arson",
        "covers": "Damaging or destroying property, including by fire.",
        "caveat": "Arson carries far more risk to life than the rest of the "
                  "category, and is not separated out.",
    },
    "possession-of-weapons": {
        "name": "Possession of weapons",
        "covers": "Carrying a firearm or bladed article, and related offences.",
        "caveat": "Possession only. A weapon actually used appears under "
                  "violence or robbery instead.",
    },
    "theft-from-the-person": {
        "name": "Theft from the person",
        "covers": "Taking property directly from a victim without force - "
                  "pickpocketing and snatch theft.",
        "caveat": "If force was used it is robbery, and sits elsewhere.",
    },
    "other-theft": {
        "name": "Other theft",
        "covers": "Theft not covered by the more specific categories.",
        "caveat": "A residual bucket - little can be inferred from it.",
    },
    "shoplifting": {"name": "Shoplifting",
                    "covers": "Theft from a shop or stall.",
                    "caveat": "Heavily dependent on whether retailers report."},
    "bicycle-theft": {"name": "Bicycle theft",
                      "covers": "Theft of a pedal cycle.",
                      "caveat": "Widely under-reported where no insurance claim follows."},
    "drugs": {"name": "Drug offences",
              "covers": "Possession, and supply or production of controlled drugs.",
              "caveat": "Largely a measure of enforcement activity rather than "
                        "of underlying drug use - more policing produces more records."},
    "public-order": {"name": "Public order",
                     "covers": "Causing fear, alarm or distress; affray; riot.",
                     "caveat": "Recording practice varies noticeably between forces."},
    "anti-social-behaviour": {
        "name": "Anti-social behaviour",
        "covers": "Reported nuisance, rowdiness and intimidation.",
        "caveat": "Not a notifiable offence. These are logged incidents, so no "
                  "outcome is ever attached and none of them carry a case ID - "
                  "which is why they always read as 'no outcome published'.",
    },
    "other-crime": {"name": "Other crime",
                    "covers": "Notifiable offences outside the listed categories.",
                    "caveat": "A residual bucket."},
    "all-crime": {"name": "All crime", "covers": "Every category combined.",
                  "caveat": "Mixing categories mixes severity - read the split, "
                            "not the total."},
}


def describe_category(code: str) -> dict:
    return CATEGORY.get(code, {"name": code.replace("-", " ").title(),
                               "covers": "Not documented in this glossary.",
                               "caveat": ""})


# --------------------------------------------------------------------------
# Outcomes
# --------------------------------------------------------------------------
# What each outcome actually means for the case, in the terms a reader cares
# about: is anyone identified, and is it over?

OUTCOME = {
    "Under investigation":
        "Live. The force is still working it.",
    "Investigation complete; no suspect identified":
        "Closed with nobody identified. This is the true cold case - the "
        "investigation ran out of leads rather than out of evidence against "
        "a named person.",
    "Unable to prosecute suspect":
        "A suspect WAS identified, but the case did not proceed - evidential "
        "difficulties, a victim withdrawing support, or time limits. Someone "
        "is known; nothing followed.",
    "Formal action is not in the public interest":
        "Identified and evidenced, but prosecuting was judged disproportionate.",
    "Further investigation is not in the public interest":
        "Stopped deliberately rather than for lack of leads.",
    "Further action is not in the public interest":
        "Stopped deliberately rather than for lack of leads.",
    "Suspect charged": "Charged and heading to court.",
    "Suspect charged as part of another case": "Folded into a linked prosecution.",
    "Awaiting court outcome": "Charged; the court has not yet reported back.",
    "Defendant sent to Crown Court": "Too serious for magistrates; committed upward.",
    "Court result unavailable":
        "The court dealt with it but the result never made it back into the "
        "published data. Not the same as unresolved.",
    "Status update unavailable":
        "The force stopped supplying updates on this record. Silence, not "
        "resolution.",
    "Offender given a caution": "Admitted, cautioned, no prosecution.",
    "Offender given a drugs possession warning": "Dealt with by warning.",
    "Offender given penalty notice": "Fixed penalty issued.",
    "Offender given community sentence": "Convicted; community sentence.",
    "Offender given suspended prison sentence": "Convicted; sentence suspended.",
    "Offender sent to prison": "Convicted and imprisoned.",
    "Offender fined": "Convicted and fined.",
    "Offender otherwise dealt with": "Convicted; disposal outside the usual list.",
    "Local resolution": "Settled informally between the parties, with police agreement.",
    "Defendant found not guilty": "Tried and acquitted.",
    "Action to be taken by another organisation":
        "Passed to another body - a regulator, or another force.",
}


def describe_outcome(name: str | None) -> str:
    if not name:
        return ("No outcome has been published for this record. Forces run "
                "about two months behind, and some never file one at all.")
    return OUTCOME.get(name, "No plain-English gloss recorded for this outcome.")


# --------------------------------------------------------------------------
# Location
# --------------------------------------------------------------------------

def describe_location(street: str) -> str:
    """
    police.uk snaps to anonymised points, and some of those points are
    category labels rather than streets. Worth saying which you are seeing.
    """
    generic = {"Nightclub", "Supermarket", "Petrol Station", "Parking Area",
               "Sports/recreation Area", "Further/higher Educational Building",
               "Shopping Area", "Pedestrian Subway", "Police Station",
               "Hospital", "Theatre/concert Hall", "Bus/coach Station",
               "Conference/exhibition Centre", "Park/open Space",
               "Prison", "Airport/airfield", "Motorway Service Area"}
    if street in generic:
        return (f"A venue type, not an address. police.uk replaces the street "
                f"with '{street}' when the snap point is a location of that "
                f"kind, so this marks the nearest such site - not a precise spot.")
    return ("The nearest anonymised map point, not an address. police.uk snaps "
            "every record to a street-segment point so individual addresses "
            "cannot be identified, so the true location is somewhere near here.")
