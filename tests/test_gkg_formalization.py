from ai_risk_trigger_inventory.risk_corpus.gkg_formalization import MATCHER_SPEC
from ai_risk_trigger_inventory.risk_corpus.gkg_revalidation import extract_gkg_document_url


def test_formal_matcher_freezes_gkg_21_document_identifier_column():
    assert MATCHER_SPEC["gkg_version"] == "2.1"
    assert MATCHER_SPEC["source_common_name_index"] == 3
    assert MATCHER_SPEC["document_url_index"] == 4
    fields = ["id", "date", "collection", "source.example", "https://source.example/article"]
    assert extract_gkg_document_url(fields) == fields[4]
    assert extract_gkg_document_url(fields) != fields[3]


def test_title_similarity_is_frozen_as_review_only():
    assert MATCHER_SPEC["title_similarity_policy"] == "REVIEW_ONLY"
    assert MATCHER_SPEC["reliable_priority"] == ["EXACT_URL", "NORMALIZED_URL", "CANONICAL_PATH"]
