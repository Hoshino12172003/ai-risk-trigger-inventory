from ai_risk_trigger_inventory.risk_corpus.gkg_revalidation import (
    GKG_DOCUMENT_URL_INDEX,
    GKG_SOURCE_COMMON_NAME_INDEX,
    expansion_days,
    extract_gkg_document_url,
)
from ai_risk_trigger_inventory.risk_corpus.gkg_unmatched_diagnostic import classify_candidate, normalize_url


def test_gkg_21_document_url_uses_column_five_not_source_common_name():
    fields = ["record", "date", "collection", "freshplaza.com", "https://freshplaza.com/article/1"]
    assert GKG_SOURCE_COMMON_NAME_INDEX == 3
    assert GKG_DOCUMENT_URL_INDEX == 4
    assert extract_gkg_document_url(fields) == "https://freshplaza.com/article/1"
    assert extract_gkg_document_url(fields) != fields[GKG_SOURCE_COMMON_NAME_INDEX]


def test_exact_url_match():
    url = "https://example.org/story"
    assert classify_candidate(url, url)[:2] == ("EXACT_URL", "HIGH")


def test_normalized_query_fragment_and_trailing_slash():
    target = "http://www.example.org/story/"
    candidate = "https://example.org/story?utm_source=test#fragment"
    assert normalize_url(target) == normalize_url(candidate)
    assert classify_candidate(target, candidate)[:2] == ("NORMALIZED_URL", "HIGH")


def test_date_expansion_is_staged_to_one_then_two_days():
    days = {"2023-04-26"}
    assert expansion_days(days, {0}) == ["2023-04-26"]
    assert expansion_days(days, {-1, 1}) == ["2023-04-25", "2023-04-27"]
    assert expansion_days(days, {-2, 2}) == ["2023-04-24", "2023-04-28"]
