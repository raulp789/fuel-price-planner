"""Helpers for normalizing US place names and states."""

import re

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_BY_NAME = {name.lower(): code for code, name in US_STATES.items()}

# Census gazetteer names carry a legal/statistical suffix, e.g. "Oklahoma City city".
_GAZETTEER_SUFFIX = re.compile(
    r"\s+(city and borough|consolidated government|metropolitan government|unified government"
    r"|urban county|charter township|city|town|village|borough|CDP|municipality|township"
    r"|plantation|CCD|gore|grant|location|purchase|unorganized territory)$"
)


def normalize_state(value):
    """Return the 2-letter code for a state code or full name, or None."""
    value = (value or "").strip()
    if value.upper() in US_STATES:
        return value.upper()
    return _STATE_BY_NAME.get(value.lower())


def normalize_place_key(name):
    """Normalize a city name so 'St. Louis', 'Saint Louis' and 'ST LOUIS' compare equal."""
    s = re.sub(r"\(.*?\)", "", name.lower()).replace(".", "")
    s = re.sub(r"\b(saint|sainte|ste)\b", "st", s)
    s = re.sub(r"\bft\b", "fort", s)
    s = re.sub(r"\bmt\b", "mount", s)
    return re.sub(r"[^a-z0-9]", "", s)


def gazetteer_base_names(raw_name):
    """Yield the plain name(s) for a gazetteer entry, e.g. 'Augusta-Richmond County ...' -> 'Augusta'."""
    name = _GAZETTEER_SUFFIX.sub("", re.sub(r"\s*\(.*?\)", "", raw_name).strip())
    yield name
    if "-" in name:
        yield name.split("-")[0]
