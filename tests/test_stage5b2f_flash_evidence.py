from copy import deepcopy

from ai_risk_trigger_inventory.risk_corpus.flash_evidence import classify_flash_evidence, reliefweb_evidence_text


def test_direct_consumer_demand_evidence_is_true():
    assert classify_flash_evidence("panic buying of food", "STRONG", source="RELIEFWEB")["flash_evidence_status"] == "TRUE_FLASH_EVIDENCE"
    assert classify_flash_evidence("increased demand for medicine", "STRONG", source="RELIEFWEB")["flash_evidence_status"] == "TRUE_FLASH_EVIDENCE"


def test_shortage_and_false_hoarding_are_not_true():
    assert classify_flash_evidence("fuel shortage", "STRONG", source="RELIEFWEB")["flash_evidence_status"] != "TRUE_FLASH_EVIDENCE"
    assert classify_flash_evidence("weapons stockpiling", "STRONG", source="GDELT")["flash_evidence_status"] == "FALSE_POSITIVE"
    assert classify_flash_evidence("land hoarding", "STRONG", source="GDELT")["flash_evidence_status"] == "FALSE_POSITIVE"


def test_reliefweb_full_body_can_recover_later_evidence():
    row = {"title":"Flood update", "body_text":"Background paragraph. Later, panic buying of food was reported."}
    title_only = classify_flash_evidence(row["title"], "STRONG", source="RELIEFWEB")
    full = classify_flash_evidence(reliefweb_evidence_text(row), "STRONG", source="RELIEFWEB")
    assert title_only["flash_evidence_status"] != "TRUE_FLASH_EVIDENCE"
    assert full["flash_evidence_status"] == "TRUE_FLASH_EVIDENCE"


def test_gdelt_geo_and_enrichment_rules():
    actor_only = classify_flash_evidence("panic buying of food", "MEDIUM", source="GDELT")
    assert actor_only["flash_evidence_status"] == "PLAUSIBLE_BUT_INSUFFICIENT"
    assert actor_only["needs_gkg_enrichment"] is True
    url_shortage = classify_flash_evidence("https://example.org/fuel-shortage", "STRONG", source="GDELT")
    assert url_shortage["flash_evidence_status"] != "TRUE_FLASH_EVIDENCE"
    false = classify_flash_evidence("military stockpiling", "STRONG", source="GDELT")
    assert false["needs_gkg_enrichment"] is False


def test_classifier_is_deterministic_and_does_not_mutate_inputs():
    text = "increased demand for water"
    before = deepcopy(text)
    assert classify_flash_evidence(text, "STRONG", source="RELIEFWEB") == classify_flash_evidence(text, "STRONG", source="RELIEFWEB")
    assert text == before
