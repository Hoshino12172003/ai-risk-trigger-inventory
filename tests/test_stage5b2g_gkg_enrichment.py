from urllib.error import HTTPError

from ai_risk_trigger_inventory.risk_corpus import gkg_enrichment
from ai_risk_trigger_inventory.risk_corpus.gkg_enrichment import classify_post_gkg, match_type, request_dates


def test_exact_and_normalized_url_matching():
    url = "https://Example.com/a/story/"
    assert match_type(url, url) == "EXACT_URL"
    assert match_type(url + "?utm_source=x", "http://example.com/a/story") == "NORMALIZED_URL"


def test_unrelated_same_date_does_not_match():
    assert match_type("https://a.example/story", "https://b.example/other", event_date="2023-01-01", gkg_date="20230101120000", event_geo="Peru", gkg_locations="Peru", themes="demand") == "NO_MATCH"


def test_target_date_boundary_is_exactly_three_days():
    assert request_dates(["2023-04-26"]) == ["2023-04-25", "2023-04-26", "2023-04-27"]
    assert request_dates(["2023-04-26", "2023-04-27"]) == ["2023-04-25", "2023-04-26", "2023-04-27", "2023-04-28"]


def _match(text: str) -> dict[str, str]:
    return {"match_type":"EXACT_URL", "V1THEMES":"", "V2THEMES":text, "V1LOCATIONS":"1#Lima#PE#", "V2LOCATIONS":"1#Lima#PE#",
        "V1PERSONS":"", "V2PERSONS":"", "V1ORGANIZATIONS":"", "V2ORGANIZATIONS":"", "V2ALLNAMES":"", "V2AMOUNTS":"", "V2QUOTATIONS":"", "V2DOCUMENTIDENTIFIER":"https://example.org"}


def test_shortage_only_does_not_become_true_and_unresolved_remains_plausible():
    event = {"SOURCEURL":"https://example.org", "flash_geo_status":"STRONG"}
    assert classify_post_gkg(event, [_match("food shortage")])[0] == "PLAUSIBLE_BUT_INSUFFICIENT"
    assert classify_post_gkg(event, [])[0] == "PLAUSIBLE_BUT_INSUFFICIENT"


def test_explicit_panic_buying_and_food_is_true():
    event = {"SOURCEURL":"https://example.org", "flash_geo_status":"STRONG"}
    assert classify_post_gkg(event, [_match("panic buying of food")])[0] == "TRUE_FLASH_EVIDENCE"


def test_missing_remote_file_is_handled_without_partial_output(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise HTTPError("https://example.org/missing.zip", 404, "not found", None, None)
    monkeypatch.setattr(gkg_enrichment, "urlopen", missing)
    destination = tmp_path / "missing.zip"
    result = gkg_enrichment._download("https://example.org/missing.zip", destination)
    assert result["download_status"] == "MISSING_REMOTE_FILE"
    assert not destination.exists()
    assert not destination.with_suffix(".zip.part").exists()
