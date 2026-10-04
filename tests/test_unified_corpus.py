from ai_risk_trigger_inventory.risk_corpus.unified_corpus import harmonize_record, normalize_iso3, parse_country_list


def test_target_country_codes_harmonize_deterministically():
    assert [normalize_iso3(code) for code in ("EC", "ECU", "CO", "COL", "PE", "PER")] == ["ECU", "ECU", "COL", "COL", "PER", "PER"]


def test_reliefweb_country_list_preserves_multi_country_scope():
    row = {"source_record_id": "1", "event_date": "2020-01-01", "primary_country_iso3": "SLV", "country_iso3_list": '["SLV", "ECU", "COL"]', "target_geo_match_basis": "COUNTRY_LIST"}
    result = harmonize_record("RELIEFWEB", "EVENT", row, "source.tsv.gz")
    assert parse_country_list(result["country_iso3_list"]) == ["COL", "ECU", "SLV"]
    assert result["is_ecuador"] == "true"
    assert result["is_andean"] == "true"


def test_reports_and_gkg_remain_document_evidence():
    report = harmonize_record("RELIEFWEB", "DOCUMENT_EVIDENCE", {"source_record_id": "r", "date_original": "2020-01-01", "country_iso3_list": '["PER"]'}, "reports.tsv.gz")
    gkg = harmonize_record("GKG", "DOCUMENT_EVIDENCE", {"GKGRECORDID": "g", "target_event_date": "2020-01-01", "target_country_code": "PE"}, "gkg.tsv.gz")
    assert report["source_record_type"] == "DOCUMENT_EVIDENCE"
    assert gkg["source_record_type"] == "DOCUMENT_EVIDENCE"
    assert gkg["primary_country_iso3"] == "PER"
