from ai_risk_trigger_inventory.risk_corpus.gkg_unmatched_diagnostic import (
    canonical_path, classify_candidate, date_offsets, normalize_url,
)


def test_url_normalization_variants():
    base = "https://example.org/story"
    for variant in ("http://example.org/story", "https://www.example.org/story", "https://example.org/story/", "https://example.org/story?utm_source=x", "https://example.org/story#section"):
        assert normalize_url(variant) == base
        assert classify_candidate(base, variant)[0] in {"EXACT_URL", "NORMALIZED_URL"}


def test_redirected_region_prefix_has_same_canonical_path():
    left = "https://freshplaza.com/north-america/article/9524011/good-sales-pomegranates"
    right = "https://www.freshplaza.com/asia/article/9524011/good-sales-pomegranates/"
    assert canonical_path(left) == canonical_path(right)
    assert classify_candidate(left, right)[0] == "CANONICAL_PATH"


def test_same_domain_different_article_is_not_a_match():
    method, confidence, _ = classify_candidate("https://example.org/news/banana-shortage", "https://example.org/news/earthquake-response")
    assert method == "SAME_DOMAIN_ONLY"
    assert confidence == "NONE"


def test_title_similarity_is_candidate_only_not_reliable():
    method, confidence, _ = classify_candidate("https://example.org/a/panic-buying-food-markets", "https://example.org/b/panic-buying-food-market-update")
    assert method == "TITLE_DOMAIN_CANDIDATE"
    assert confidence == "REVIEW_ONLY"


def test_date_window_order_stops_at_two_days():
    assert date_offsets() == (0, -1, 1, -2, 2)
