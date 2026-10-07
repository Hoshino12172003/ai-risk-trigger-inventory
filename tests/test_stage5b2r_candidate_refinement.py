from copy import deepcopy

from ai_risk_trigger_inventory.risk_corpus.candidate_refinement import refine_anchor, refine_candidate


def row(text: str, *, flash=False, regional=False, transport=False, geo="STRONG", basis="ACTION_GEO"):
    return {"source":"GDELT", "source_record_type":"EVENT", "source_record_id":"1",
        "title_text_excerpt":text, "candidate_flash_demand":str(flash).lower(),
        "candidate_regional_emergency":str(regional).lower(), "candidate_transport_network":str(transport).lower(),
        "transport_asset_hits":"[]", "transport_disruption_hits":"[]", "structured_metadata_hits":"[]",
        "geo_evidence_strength":geo, "geo_match_basis":basis}


def test_flash_strict_rules():
    assert refine_candidate(row("panic buying of food", flash=True))["flash_refinement_tier"] == "HIGH_CONFIDENCE"
    assert refine_candidate(row("food shortage", flash=True))["flash_refinement_tier"] != "HIGH_CONFIDENCE"
    assert refine_candidate(row("weapons stockpiling", flash=True))["candidate_tier"] == "DEFERRED"


def test_regional_geo_and_generic_rules():
    assert refine_candidate(row("major flood", regional=True))["regional_refinement_tier"] == "HIGH_CONFIDENCE"
    assert refine_candidate(row("major flood", regional=True, geo="MEDIUM", basis="ACTOR_GEO"))["regional_refinement_tier"] != "HIGH_CONFIDENCE"
    assert refine_candidate(row("emergency", regional=True))["regional_refinement_tier"] != "HIGH_CONFIDENCE"


def test_transport_requires_asset_and_disruption():
    both = row("road closed", transport=True)
    both["transport_asset_hits"]='["road"]'; both["transport_disruption_hits"]='["closed"]'
    assert refine_candidate(both)["transport_refinement_tier"] == "HIGH_CONFIDENCE"
    route = row("route mentioned", transport=True); route["transport_asset_hits"]='["route"]'
    assert refine_candidate(route)["transport_refinement_tier"] != "HIGH_CONFIDENCE"


def test_structured_anchor_confidence():
    usgs = row("earthquake", regional=True); usgs.update(source="USGS", source_record_type="EARTHQUAKE", evidence_role="EVENT_ANCHOR")
    assert refine_anchor(usgs)["anchor_confidence"] == "HIGH"
    cop = deepcopy(usgs); cop.update(source="COPERNICUS_EMS", title_text_excerpt="Preparedness risk assessment")
    assert refine_anchor(cop)["anchor_confidence"] == "MEDIUM"
