"""Frozen filters and provenance helpers for the Stage-5A supplement."""

from __future__ import annotations

from collections.abc import Iterable
import re


BATCH_ID = "STAGE5A_SUPPLEMENT_001"
GDELT_NATIVE_START = "2015-02-19"
TARGET_COUNTRY_CODES = {"EC": "Ecuador", "CO": "Colombia", "PE": "Peru"}

REGIONAL_TERMS = (
    "flood", "flash flood", "landslide", "earthquake", "volcan", "severe storm",
    "extreme rainfall", "wildfire", "drought", "public emergency", "natural disaster",
)
TRANSPORT_TERMS = (
    "road closure", "highway closure", "bridge closure", "transport", "logistics",
    "port disruption", "airport disruption", "rail disruption", "road damage",
    "infrastructure disruption", "supply route",
)
DEMAND_TERMS = (
    "demand surge", "panic buying", "shortage", "stockout", "consumer rush",
    "sales surge", "shopping surge", "promotion", "flash sale", "retail event",
    "holiday demand", "emergency purchasing",
)
RISK_TERMS = REGIONAL_TERMS + TRANSPORT_TERMS + DEMAND_TERMS


def _normalized_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def matching_terms(values: Iterable[str]) -> tuple[str, ...]:
    text = _normalized_text(" ".join(values))
    return tuple(term for term in RISK_TERMS if _normalized_text(term) in text)


def event_geographies(fields: list[str]) -> tuple[str, ...]:
    """Return target countries supported by Event structured geo fields."""
    if len(fields) < 68:
        return ()
    codes = {fields[index].strip().upper() for index in (47, 54, 61)}
    names = " ".join(fields[index].lower() for index in (46, 53, 60))
    countries = {
        country for code, country in TARGET_COUNTRY_CODES.items()
        if code in codes or country.lower() in names
    }
    return tuple(sorted(countries))


def filter_event_row(fields: list[str]) -> dict[str, object] | None:
    countries = event_geographies(fields)
    if not countries:
        return None
    terms = matching_terms((fields[67],))
    if not terms:
        return None
    return {
        "record_id": fields[0],
        "date": fields[1],
        "countries": countries,
        "matched_retrieval_terms": terms,
        "geography_provenance": "STRUCTURED_EVENT_GEO",
    }


def gkg_geographies(fields: list[str]) -> tuple[tuple[str, ...], str | None]:
    """Prefer structured GKG locations; allow separately marked URL-only matches."""
    if len(fields) < 15:
        return (), None
    location_text = " ".join((fields[9], fields[10]))
    structured = {
        country for code, country in TARGET_COUNTRY_CODES.items()
        if re.search(rf"#{code}(?:#|;|$)", location_text, re.IGNORECASE)
        or country.lower() in location_text.lower()
    }
    if structured:
        return tuple(sorted(structured)), "STRUCTURED_GKG_LOCATION"
    url = fields[4].lower()
    textual = {country for country in TARGET_COUNTRY_CODES.values() if country.lower() in url}
    if textual:
        return tuple(sorted(textual)), "TEXTUAL_DOCUMENT_URL"
    return (), None


def filter_gkg_row(fields: list[str]) -> dict[str, object] | None:
    if len(fields) < 16:
        return None
    countries, geography_provenance = gkg_geographies(fields)
    if not countries:
        return None
    terms = matching_terms((fields[7], fields[8], fields[4]))
    if not terms:
        return None
    return {
        "record_id": fields[0],
        "date": fields[1][:8],
        "countries": countries,
        "matched_retrieval_terms": terms,
        "geography_provenance": geography_provenance,
    }


def gdelt_anchor_days() -> tuple[str, ...]:
    """Frozen systematic sample: native start, annual anchors, requested end."""
    return (
        "20150219",
        *(f"{year}0701" for year in range(2016, 2026)),
        "20260831",
    )


def gdelt_monthly_anchor_days() -> tuple[str, ...]:
    anchors = ["20150219"]
    year, month = 2015, 3
    while (year, month) <= (2026, 8):
        anchors.append(f"{year:04d}{month:02d}01")
        month += 1
        if month == 13:
            year += 1
            month = 1
    anchors.append("20260831")
    return tuple(anchors)
