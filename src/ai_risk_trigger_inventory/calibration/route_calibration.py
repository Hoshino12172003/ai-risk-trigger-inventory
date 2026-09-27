"""Frozen Stage-3D literature-anchored route-risk calibration lookup."""

from __future__ import annotations

from types import MappingProxyType
import re

from ai_risk_trigger_inventory.calibration.event_mapping import EventMetadata


ROUTE_CALIBRATION = MappingProxyType({
    "NORMAL": {
        "severity_class": "NORMAL",
        "delta_A_lower": 0.00,
        "delta_A_main": 0.00,
        "delta_A_upper": 0.00,
        "interpretation": "No effective route-service loss.",
        "calibration_basis": "MODEL_DEFINITION",
        "evidence_status": "DEFINITION_SUPPORTED",
        "provenance": "DEFINITION",
    },
    "MODERATE_DISRUPTION": {
        "severity_class": "MODERATE",
        "delta_A_lower": 0.10,
        "delta_A_main": 0.25,
        "delta_A_upper": 0.40,
        "interpretation": "Partial planning-period effective route-service loss.",
        "calibration_basis": "CROSS_EVIDENCE_LITERATURE_RANGE",
        "evidence_status": "LITERATURE_ANCHORED",
        "provenance": "LITERATURE_ANCHORED_CROSS_REGION",
    },
    "SEVERE_DISRUPTION": {
        "severity_class": "HIGH_OR_EXTREME",
        "delta_A_lower": 0.40,
        "delta_A_main": 0.60,
        "delta_A_upper": 0.80,
        "interpretation": "Large planning-period effective route-service loss short of confirmed closure.",
        "calibration_basis": "CROSS_EVIDENCE_LITERATURE_RANGE",
        "evidence_status": "LITERATURE_ANCHORED",
        "provenance": "LITERATURE_ANCHORED_CROSS_REGION",
    },
    "CLOSURE": {
        "severity_class": "EXPLICIT_CLOSURE",
        "delta_A_lower": 1.00,
        "delta_A_main": 1.00,
        "delta_A_upper": 1.00,
        "interpretation": "Confirmed warehouse-region arc is unavailable for the planning period.",
        "calibration_basis": "MODEL_DEFINITION",
        "evidence_status": "DEFINITION_SUPPORTED",
        "provenance": "DEFINITION",
    },
})

ROUTE_SENSITIVITY_GRID = (
    {"scenario_label": "LOW", "moderate_delta_A": 0.10, "severe_delta_A": 0.40, "closure_delta_A": 1.00},
    {"scenario_label": "MAIN", "moderate_delta_A": 0.25, "severe_delta_A": 0.60, "closure_delta_A": 1.00},
    {"scenario_label": "HIGH", "moderate_delta_A": 0.40, "severe_delta_A": 0.80, "closure_delta_A": 1.00},
)

ALLOWED_EVIDENCE_LEVELS = frozenset({"LEVEL_1", "LEVEL_2", "LEVEL_3"})
ALLOWED_TRANSFER_ROLES = frozenset({
    "LOCAL_CONTEXT_SUPPORT",
    "REGIONAL_TRANSFER_SUPPORT",
    "QUANTITATIVE_RANGE_ANCHOR",
})


def route_calibration_rows() -> list[dict[str, object]]:
    return [
        {"route_state": route_state, **parameters}
        for route_state, parameters in ROUTE_CALIBRATION.items()
    ]


def has_explicit_closure_evidence(event_subtype: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", event_subtype.lower()).strip()
    tokens = set(normalized.split())
    closure_with_qualifier = "closure" in tokens and bool(tokens & {"full", "confirmed"})
    return closure_with_qualifier or any(
        phrase in normalized
        for phrase in ("road completely closed", "route unavailable")
    )


def route_state_for_metadata(metadata: EventMetadata) -> str:
    if has_explicit_closure_evidence(metadata.event_subtype):
        return "CLOSURE"
    if metadata.severity == "MODERATE":
        return "MODERATE_DISRUPTION"
    if metadata.severity in {"HIGH", "EXTREME"}:
        return "SEVERE_DISRUPTION"
    raise ValueError("route calibration requires MODERATE, HIGH, EXTREME, or explicit closure evidence")


def lookup_route_calibration(route_state: str) -> dict[str, object]:
    if route_state not in ROUTE_CALIBRATION:
        raise ValueError(f"unknown route state: {route_state}")
    return {"route_state": route_state, **ROUTE_CALIBRATION[route_state]}


def map_event_to_route_calibration(metadata: EventMetadata) -> list[dict[str, object]]:
    if metadata.risk_type not in {
        "REGIONAL_EMERGENCY_DISRUPTION", "TRANSPORT_NETWORK_DISRUPTION",
    }:
        raise ValueError(f"risk type has no route calibration: {metadata.risk_type}")
    calibration = lookup_route_calibration(route_state_for_metadata(metadata))
    return [{
        "event_id": metadata.event_id,
        "warehouse_id": arc.warehouse_id,
        "region_id": arc.region_id,
        "impact_scope": arc.impact_scope,
        "severity": metadata.severity,
        "expected_duration": metadata.expected_duration,
        **calibration,
    } for arc in metadata.affected_arcs]


def evidence_rows() -> list[dict[str, object]]:
    rows = [
        {
            "evidence_id": "ECUADOR_IDB_2003_ROAD_NETWORK",
            "geography": "Ecuador",
            "evidence_level": "LEVEL_1",
            "hazard_type": "Flood; landslide; earthquake; volcanic activity",
            "transport_effect": "Road-network disruption and loss of network functionality affect economic flows.",
            "reported_metric": "Qualitative road-network functionality and flow-loss evidence",
            "reported_value_or_range": "No transferable percentage reported",
            "source_title": "Natural Disaster Management and the Road Network in Ecuador: Policy Issues and Recommendations",
            "source_year": 2003,
            "source_url_or_doi": "https://publications.iadb.org/publications/english/document/Natural-Disaster-Management-and-the-Road-Network-in-Ecuador-Policy-Issues-and-Recommendations.pdf",
            "transfer_role": "LOCAL_CONTEXT_SUPPORT",
            "notes": "Supports operational relevance in Ecuador; does not estimate delta_A.",
        },
        {
            "evidence_id": "ECUADOR_IDB_2019_CRITICAL_POINTS",
            "geography": "Ecuador",
            "evidence_level": "LEVEL_1",
            "hazard_type": "Rainfall-induced landslide",
            "transport_effect": "Partial or total road closure can occur at identified critical points.",
            "reported_metric": "Critical locations with potential partial or total closure",
            "reported_value_or_range": "83 critical points",
            "source_title": "Inter-American Development Bank Sustainability Report 2019",
            "source_year": 2019,
            "source_url_or_doi": "https://publications.iadb.org/publications/english/document/Inter-American-Development-Bank-Sustainability-Report-2019.pdf",
            "transfer_role": "LOCAL_CONTEXT_SUPPORT",
            "notes": "Supports partial/severe/closure states; not a route-loss percentage estimate.",
        },
        {
            "evidence_id": "ECUADOR_IDB_2022_LANDSLIDE_RISK",
            "geography": "Zamora Chinchipe, Ecuador",
            "evidence_level": "LEVEL_1",
            "hazard_type": "Rainfall; landslide",
            "transport_effect": "Critical road locations require disaster-risk mitigation alternatives.",
            "reported_metric": "Indicative road-project hazard and critical-zone assessment",
            "reported_value_or_range": "No transferable percentage reported",
            "source_title": "Analisis de la inversion publica para la reduccion de riesgo de desastres: estudio de caso en Ecuador",
            "source_year": 2022,
            "source_url_or_doi": "https://doi.org/10.18235/0003916",
            "transfer_role": "LOCAL_CONTEXT_SUPPORT",
            "notes": "Ecuador road-sector context only; does not directly identify effective service loss.",
        },
        {
            "evidence_id": "LAC_IDB_2023_PAN_AMERICAN_CLOSURE",
            "geography": "Colombia-Ecuador Andean corridor",
            "evidence_level": "LEVEL_2",
            "hazard_type": "Landslide",
            "transport_effect": "Total closure of a low-redundancy Pan-American corridor isolated the region and forced major detours.",
            "reported_metric": "Additional travel caused by interruption",
            "reported_value_or_range": "400 km or more in low-redundancy settings",
            "source_title": "Transportation 2050: Pathways to Decarbonization and Climate Resilience in Latin America and the Caribbean",
            "source_year": 2023,
            "source_url_or_doi": "https://publications.iadb.org/publications/english/document/Transportation-2050-pathways-to-decarbonization-and-climate-resilience-in-Latin-America-and-the-Caribbean.pdf",
            "transfer_role": "REGIONAL_TRANSFER_SUPPORT",
            "notes": "Supports severe disruption and closure relevance in the Andean region; not a delta_A estimate.",
        },
        {
            "evidence_id": "GLOBAL_WB_2022_ROUTE_FAILURE",
            "geography": "Global, 2,564 settlement clusters",
            "evidence_level": "LEVEL_3",
            "hazard_type": "Pluvial and fluvial flood",
            "transport_effect": "Flood exposure causes increasing route failures across lower- and higher-intensity scenarios.",
            "reported_metric": "Mean share of simulated origin-destination routes that fail",
            "reported_value_or_range": "11.58% (5-year) to 65.97% (1,000-year) at 30 cm threshold",
            "source_title": "Mobility and Resilience: A Global Assessment of Flood Impacts on Road Transportation Networks",
            "source_year": 2022,
            "source_url_or_doi": "https://documents.worldbank.org/curated/en/099552305172228687/pdf/IDU0e38cb88d018d704a8e08a220c09ce9f1be91.pdf",
            "transfer_role": "QUANTITATIVE_RANGE_ANCHOR",
            "notes": "Route-failure share is not delta_A; anchors lower-to-higher effective service-loss regimes.",
        },
        {
            "evidence_id": "PHILIPPINES_MAMUYAC_2024_FLOW",
            "geography": "Metro Manila, Philippines",
            "evidence_level": "LEVEL_3",
            "hazard_type": "Urban flood and lane closure",
            "transport_effect": "Increasing lane closures reduce observed vehicle flow.",
            "reported_metric": "Vehicle-flow reduction per lane kilometer from 433 samples",
            "reported_value_or_range": "40% to 70%",
            "source_title": "The Effects of Flood-induced Lane Reduction on the Capacity of a Road Network",
            "source_year": 2024,
            "source_url_or_doi": "https://doi.org/10.5109/7323361",
            "transfer_role": "QUANTITATIVE_RANGE_ANCHOR",
            "notes": "Flow reduction is not delta_A; supports the severe-range anchor and partial-loss interpretation.",
        },
        {
            "evidence_id": "USA_HARVEY_2022_COMPOUND_FAILURE",
            "geography": "Harris County, Texas, United States",
            "evidence_level": "LEVEL_3",
            "hazard_type": "Hurricane flood",
            "transport_effect": "Small direct/compound failures can produce larger network-connectivity losses.",
            "reported_metric": "Flood-induced compound failure and decrease in giant-component size",
            "reported_value_or_range": "2.2% compound failure associated with 17.7% giant-component decrease",
            "source_title": "Modest flooding can trigger catastrophic road network collapse due to compound failure",
            "source_year": 2022,
            "source_url_or_doi": "https://doi.org/10.1038/s43247-022-00366-0",
            "transfer_role": "QUANTITATIVE_RANGE_ANCHOR",
            "notes": "Demonstrates nonlinear network effects; it is not converted directly into delta_A.",
        },
    ]
    if not all(row["evidence_level"] in ALLOWED_EVIDENCE_LEVELS for row in rows):
        raise RuntimeError("invalid evidence level")
    if not all(row["transfer_role"] in ALLOWED_TRANSFER_ROLES for row in rows):
        raise RuntimeError("invalid evidence transfer role")
    return rows
