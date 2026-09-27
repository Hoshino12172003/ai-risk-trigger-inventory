"""Auditable event-to-severity-to-impact mapping over frozen Stage-3A results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from types import MappingProxyType

import numpy as np
import pandas as pd


SEVERITY_TO_QUANTILE = MappingProxyType({
    "MODERATE": 0.75,
    "HIGH": 0.90,
    "EXTREME": 0.95,
})
ALLOWED_SEVERITIES = frozenset((*SEVERITY_TO_QUANTILE, "UNKNOWN"))

DEMAND_STRATA = MappingProxyType({
    "D1_PROMOTION": ("event_proxy", "P1_PROMOTION"),
    "D2_HOLIDAY_EVENT": ("event_proxy", "P2_HOLIDAY_EVENT"),
    "D3_PROMOTION_HOLIDAY_OVERLAP": (
        "event_proxy", "P3_PROMOTION_HOLIDAY_OVERLAP",
    ),
    "D4_GENERIC_EVENT_PROXY": ("overall", "ALL_EVENT_PROXIES"),
})

ROUTE_STATES = MappingProxyType({
    "NORMAL": {
        "severity": "NORMAL",
        "delta_A": 0.0,
        "interpretation": "No effective route-service loss.",
        "evidence_source": "Route-state definition",
        "provenance": "DEFINITION",
        "status": "DEFINITION_SUPPORTED",
    },
    "MODERATE_DISRUPTION": {
        "severity": "MODERATE",
        "delta_A": None,
        "interpretation": "Partial route-service loss; magnitude is unresolved.",
        "evidence_source": "No verified external route evidence",
        "provenance": "PENDING_EXTERNAL_EVIDENCE",
        "status": "PENDING_EXTERNAL_EVIDENCE",
    },
    "SEVERE_DISRUPTION": {
        "severity": "HIGH_OR_EXTREME",
        "delta_A": None,
        "interpretation": "Large route-service loss; magnitude is unresolved.",
        "evidence_source": "No verified external route evidence",
        "provenance": "PENDING_EXTERNAL_EVIDENCE",
        "status": "PENDING_EXTERNAL_EVIDENCE",
    },
    "CLOSURE": {
        "severity": "CLOSURE",
        "delta_A": 1.0,
        "interpretation": "Confirmed full warehouse-region arc closure.",
        "evidence_source": "Route-state definition",
        "provenance": "DEFINITION",
        "status": "DEFINITION_SUPPORTED",
    },
})

STAGE1_ROUTE_STRESS_REFERENCES = (0.25, 0.50, 0.75)


@dataclass(frozen=True)
class AffectedArc:
    warehouse_id: str
    region_id: str
    impact_scope: str = "WAREHOUSE_REGION_ARC"

    def __post_init__(self) -> None:
        if not self.warehouse_id or not self.region_id:
            raise ValueError("affected arc requires warehouse_id and region_id")
        if self.impact_scope != "WAREHOUSE_REGION_ARC":
            raise ValueError("route impacts must target a warehouse-region arc")


@dataclass(frozen=True)
class EventMetadata:
    event_id: str
    risk_type: str
    event_subtype: str
    region_scope: tuple[str, ...]
    severity: str
    start_time: str
    expected_duration: str
    evidence_source: str
    affected_arcs: tuple[AffectedArc, ...] = ()

    def __post_init__(self) -> None:
        if self.severity not in ALLOWED_SEVERITIES:
            raise ValueError(f"invalid severity: {self.severity}")
        for field_name in (
            "event_id", "risk_type", "event_subtype", "start_time",
            "expected_duration", "evidence_source",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"event metadata requires {field_name}")


class DemandCalibrationLookup:
    """Read-only lookup assembled from committed Stage-3A artifact tables."""

    def __init__(
        self,
        quantiles: pd.DataFrame,
        bootstrap: pd.DataFrame,
        coverage: pd.DataFrame,
    ) -> None:
        self._quantiles = quantiles.copy()
        self._bootstrap = bootstrap.copy()
        self._coverage = coverage.copy()

    @classmethod
    def from_stage3a_artifacts(cls, artifact_dir: Path) -> "DemandCalibrationLookup":
        return cls(
            pd.read_csv(artifact_dir / "empirical_quantiles.csv"),
            pd.read_csv(artifact_dir / "quantile_bootstrap_ci.csv"),
            pd.read_csv(artifact_dir / "heldout_coverage.csv"),
        )

    def lookup(self, calibration_stratum: str, severity: str) -> dict[str, object]:
        if severity == "UNKNOWN":
            return {
                "calibration_stratum": calibration_stratum,
                "quantile_level": None,
                "delta_D": None,
                "sample_size": None,
                "bootstrap_ci_lower": None,
                "bootstrap_ci_upper": None,
                "heldout_coverage": None,
                "source": "Stage-3A artifacts",
                "status": "UNRESOLVED",
                "resolution_code": "CALIBRATION_UNRESOLVED",
            }
        if severity not in SEVERITY_TO_QUANTILE:
            raise ValueError(f"invalid severity: {severity}")
        if calibration_stratum not in DEMAND_STRATA:
            raise ValueError(f"invalid demand calibration stratum: {calibration_stratum}")
        dimension, value = DEMAND_STRATA[calibration_stratum]
        quantile_level = SEVERITY_TO_QUANTILE[severity]
        row = self._quantiles[
            (self._quantiles["population"] == "POSITIVE_UPLIFT_EVENT_PROXY_P1_P2_P3")
            & (self._quantiles["group_dimension"] == dimension)
            & (self._quantiles["group_value"] == value)
        ]
        if len(row) != 1:
            raise ValueError(f"Stage-3A quantile row not uniquely identified for {calibration_stratum}")
        row = row.iloc[0]
        q_column = f"q{int(quantile_level * 100)}"
        if q_column == "q99":
            raise RuntimeError("q99 is descriptive only and cannot enter severity mapping")
        bootstrap = self._bootstrap[
            (self._bootstrap["group_dimension"] == dimension)
            & (self._bootstrap["group_value"] == value)
            & np.isclose(self._bootstrap["quantile_level"], quantile_level)
        ]
        coverage = self._coverage[
            (self._coverage["group_dimension"] == dimension)
            & (self._coverage["group_value"] == value)
            & np.isclose(self._coverage["quantile_level"], quantile_level)
        ]
        if len(bootstrap) != 1 or len(coverage) != 1:
            raise ValueError(f"Stage-3A uncertainty rows not uniquely identified for {calibration_stratum}")
        return {
            "calibration_stratum": calibration_stratum,
            "quantile_level": quantile_level,
            "delta_D": float(row[q_column]),
            "sample_size": int(row["sample_size"]),
            "bootstrap_ci_lower": float(bootstrap.iloc[0]["ci_lower"]),
            "bootstrap_ci_upper": float(bootstrap.iloc[0]["ci_upper"]),
            "heldout_coverage": float(coverage.iloc[0]["observed_coverage"]),
            "source": "Stage-3A empirical_quantiles/bootstrap/heldout_coverage artifacts",
            "status": "DATA_SUPPORTED",
            "resolution_code": "RESOLVED_FROM_STAGE3A",
        }


def demand_stratum_for_event(metadata: EventMetadata) -> str:
    if metadata.risk_type == "REGIONAL_EMERGENCY_DISRUPTION":
        return "D4_GENERIC_EVENT_PROXY"
    if metadata.risk_type != "FLASH_DEMAND_SURGE":
        raise ValueError(f"risk type has no demand mapping: {metadata.risk_type}")
    normalized = re.sub(r"[^a-z0-9]+", " ", metadata.event_subtype.lower()).strip()
    tokens = set(normalized.split())
    promotion = bool(tokens & {"promotion", "campaign", "sale"})
    holiday = bool(tokens & {"holiday", "festival"}) or any(
        phrase in normalized for phrase in ("public event", "scheduled large event")
    )
    if promotion and holiday:
        return "D3_PROMOTION_HOLIDAY_OVERLAP"
    if promotion:
        return "D1_PROMOTION"
    if holiday:
        return "D2_HOLIDAY_EVENT"
    return "D4_GENERIC_EVENT_PROXY"


def map_event_to_demand_impact(
    metadata: EventMetadata,
    lookup: DemandCalibrationLookup,
) -> dict[str, object]:
    stratum = demand_stratum_for_event(metadata)
    result = lookup.lookup(stratum, metadata.severity)
    regional = metadata.risk_type == "REGIONAL_EMERGENCY_DISRUPTION"
    result.update({
        "event_id": metadata.event_id,
        "risk_type": metadata.risk_type,
        "event_subtype": metadata.event_subtype,
        "severity": metadata.severity,
        "provenance": "INDIRECT_RETAIL_PROXY" if regional else "FAVORITA_RETAIL_DEMAND_SURGE_PROXY",
    })
    if regional and result["status"] != "UNRESOLVED":
        result["status"] = "INDIRECT_PROXY"
    return result


def route_state_for_event(metadata: EventMetadata) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", metadata.event_subtype.lower()).strip()
    if any(phrase in normalized for phrase in ("full road closure", "confirmed closure")):
        return "CLOSURE"
    if metadata.severity == "MODERATE":
        return "MODERATE_DISRUPTION"
    if metadata.severity in {"HIGH", "EXTREME"}:
        return "SEVERE_DISRUPTION"
    if metadata.severity == "UNKNOWN":
        return "UNRESOLVED"
    raise ValueError(f"invalid severity: {metadata.severity}")


def route_severity_rows() -> list[dict[str, object]]:
    rows = [
        {"route_state": state, **definition, "mapping_role": "FORMAL_STATE"}
        for state, definition in ROUTE_STATES.items()
    ]
    rows.extend({
        "route_state": "REFERENCE_ONLY",
        "severity": "NOT_APPLICABLE",
        "delta_A": value,
        "interpretation": "Stage-1 preregistered stress-test reference; not a formal severity mapping.",
        "evidence_source": "Stage-1 stress test",
        "provenance": "PREREGISTERED_STAGE1_STRESS_MAGNITUDE",
        "status": "STRESS_TEST_REFERENCE",
        "mapping_role": "REFERENCE_ONLY",
    } for value in STAGE1_ROUTE_STRESS_REFERENCES)
    return rows


def map_event_to_route_impacts(metadata: EventMetadata) -> list[dict[str, object]]:
    if metadata.risk_type not in {
        "TRANSPORT_NETWORK_DISRUPTION", "REGIONAL_EMERGENCY_DISRUPTION",
    }:
        raise ValueError(f"risk type has no route mapping: {metadata.risk_type}")
    state = route_state_for_event(metadata)
    if state == "UNRESOLVED":
        definition = {
            "delta_A": None,
            "provenance": "CALIBRATION_UNRESOLVED",
            "status": "CALIBRATION_UNRESOLVED",
        }
    else:
        definition = ROUTE_STATES[state]
    return [{
        "event_id": metadata.event_id,
        "warehouse_id": arc.warehouse_id,
        "region_id": arc.region_id,
        "impact_scope": arc.impact_scope,
        "severity": metadata.severity,
        "route_state": state,
        "delta_A": definition["delta_A"],
        "provenance": definition["provenance"],
        "status": definition["status"],
        "expected_duration": metadata.expected_duration,
    } for arc in metadata.affected_arcs]


def build_demand_severity_rows(lookup: DemandCalibrationLookup) -> list[dict[str, object]]:
    rows = []
    flash_subtypes = {
        "D1_PROMOTION": "PROMOTION_OR_CAMPAIGN",
        "D2_HOLIDAY_EVENT": "HOLIDAY_OR_SCHEDULED_EVENT",
        "D3_PROMOTION_HOLIDAY_OVERLAP": "PROMOTION_HOLIDAY_OVERLAP",
        "D4_GENERIC_EVENT_PROXY": "UNCLASSIFIED_FLASH_EVENT",
    }
    for stratum, subtype in flash_subtypes.items():
        for severity in SEVERITY_TO_QUANTILE:
            result = lookup.lookup(stratum, severity)
            rows.append({
                "risk_type": "FLASH_DEMAND_SURGE",
                "event_subtype": subtype,
                "severity": severity,
                **result,
                "provenance": "FAVORITA_RETAIL_DEMAND_SURGE_PROXY",
            })
    for severity in SEVERITY_TO_QUANTILE:
        result = lookup.lookup("D4_GENERIC_EVENT_PROXY", severity)
        rows.append({
            "risk_type": "REGIONAL_EMERGENCY_DISRUPTION",
            "event_subtype": "REGIONAL_EMERGENCY",
            "severity": severity,
            **result,
            "provenance": "INDIRECT_RETAIL_PROXY",
            "status": "INDIRECT_PROXY",
        })
    return rows


def metadata_as_dict(metadata: EventMetadata) -> dict[str, object]:
    return asdict(metadata)
