"""Deterministic Stage 5B-2R high-confidence candidate refinement."""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
import csv
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path
import re
from typing import Any, Iterable, Iterator
import unicodedata

from .candidate_screening import file_sha256, read_tsv


TIERS = ("HIGH_CONFIDENCE", "SECONDARY", "DEFERRED")
FAMILIES = {
    "FLASH_DEMAND_SURGE": "candidate_flash_demand",
    "REGIONAL_EMERGENCY_DISRUPTION": "candidate_regional_emergency",
    "TRANSPORT_NETWORK_DISRUPTION": "candidate_transport_network",
}
REFINEMENT_FIELDS = [
    "candidate_tier", "flash_refinement_tier", "regional_refinement_tier",
    "transport_refinement_tier", "anchor_confidence", "refinement_reason",
    "refinement_rule_hits", "refinement_exclusion_flags",
]

FLASH_EXPLICIT = (
    "demand surge", "demand spike", "sharp increase in demand", "increased demand",
    "surge in purchases", "panic buying", "rush to buy", "buying surge", "sales spike",
    "aumento de la demanda", "incremento de la demanda", "pico de demanda",
    "compras de pánico", "aumento de compras", "incremento de compras",
    "incremento de ventas", "aumento de ventas",
)
CONSUMER_CONTEXT = (
    "food", "water", "medicine", "fuel", "groceries", "supermarket", "retail",
    "essential goods", "alimentos", "agua", "medicamentos", "combustible",
    "supermercado", "bienes esenciales",
)
SHORTAGE_TERMS = ("shortage", "scarcity", "stockout", "out of stock", "escasez", "agotado", "desabastecimiento")
HOARDING_TERMS = ("hoarding", "stockpiling", "acaparamiento")
EXCLUDED_STOCKPILING = (
    "weapons stockpiling", "land hoarding", "financial hoarding", "equipment stockpiling",
    "strategic reserve", "government reserve",
)
REGIONAL_SPECIFIC = (
    "flood", "earthquake", "landslide", "mudslide", "storm", "wildfire", "eruption",
    "drought", "tsunami", "epidemic", "outbreak", "inundación", "inundaciones",
    "terremoto", "sismo", "deslizamiento", "huaico", "tormenta", "huracán", "ciclón",
    "incendio forestal", "erupción", "volcán", "sequía", "brote",
)
REGIONAL_GENERIC = ("emergency", "disaster", "crisis", "emergencia", "desastre", "crisis")
PREPAREDNESS = (
    "preparedness", "preparedness-only", "drill", "exercise", "warning", "risk assessment",
    "simulacro", "preparación", "alerta preventiva",
)
BACKGROUND = ("background", "overview", "retrospective", "historical reference", "antecedentes", "panorama")
TRANSPORT_EXCLUSIONS = (
    "migration route", "political route", "funding freeze", "program closure",
    "facility closure", "ruta migratoria", "cierre del programa",
)


def _norm(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "")).lower()


def _terms(text: str, terms: Iterable[str]) -> list[str]:
    normalized = re.sub(r"[-_/+%]+", " ", _norm(text))
    hits = []
    for term in terms:
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", normalized):
            hits.append(term)
    return sorted(set(hits))


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def _dump(values: Iterable[str]) -> str:
    return json.dumps(sorted(set(value for value in values if value)), ensure_ascii=False, separators=(",", ":"))


def _active(row: dict[str, str], family: str) -> bool:
    return row.get(FAMILIES[family], "").lower() == "true"


def refine_candidate(row: dict[str, str]) -> dict[str, str]:
    """Refine a GDELT or ReliefWeb report without changing its source fields."""
    text = row.get("title_text_excerpt", "")
    geo = row.get("geo_evidence_strength", "")
    exclusions: list[str] = []
    hits: list[str] = []

    flash_tier = "NOT_CANDIDATE"
    if _active(row, "FLASH_DEMAND_SURGE"):
        explicit = _terms(text, FLASH_EXPLICIT)
        context = _terms(text, CONSUMER_CONTEXT)
        shortage = _terms(text, SHORTAGE_TERMS)
        hoarding = _terms(text, HOARDING_TERMS)
        forbidden = _terms(text, EXCLUDED_STOCKPILING)
        hits.extend(f"FLASH_EXPLICIT:{item}" for item in explicit)
        hits.extend(f"CONSUMER_CONTEXT:{item}" for item in context)
        if forbidden:
            flash_tier = "DEFERRED"
            exclusions.append("GENERIC_HOARDING")
        elif explicit and context and geo == "STRONG":
            flash_tier = "HIGH_CONFIDENCE"
        elif shortage and not explicit:
            flash_tier = "SECONDARY" if context else "DEFERRED"
            exclusions.append("SHORTAGE_ONLY")
        elif hoarding and not explicit:
            flash_tier = "DEFERRED"
            exclusions.append("GENERIC_HOARDING")
        else:
            flash_tier = "SECONDARY" if explicit else "DEFERRED"
            exclusions.append("WRONG_GEOGRAPHY" if explicit and geo != "STRONG" else "WEAK_KEYWORD")

    regional_tier = "NOT_CANDIDATE"
    if _active(row, "REGIONAL_EMERGENCY_DISRUPTION"):
        specific = _terms(text, REGIONAL_SPECIFIC)
        generic = _terms(text, REGIONAL_GENERIC)
        preparedness = _terms(text, PREPAREDNESS)
        background = _terms(text, BACKGROUND)
        hits.extend(f"REGIONAL_SPECIFIC:{item}" for item in specific)
        if preparedness:
            regional_tier = "SECONDARY"
            exclusions.append("PREPAREDNESS_ONLY")
        elif specific and geo == "STRONG" and not background:
            regional_tier = "HIGH_CONFIDENCE"
        elif specific:
            regional_tier = "SECONDARY"
            exclusions.append("ACTOR_GEO_ONLY" if row.get("geo_match_basis") == "ACTOR_GEO" else "WRONG_GEOGRAPHY")
        elif generic:
            regional_tier = "SECONDARY" if geo == "STRONG" else "DEFERRED"
            exclusions.append("WEAK_EMERGENCY_WORD")
        else:
            regional_tier = "DEFERRED"
            exclusions.append("BACKGROUND_MENTION")
        if background:
            exclusions.append("BACKGROUND_MENTION")
            if regional_tier == "HIGH_CONFIDENCE":
                regional_tier = "SECONDARY"

    transport_tier = "NOT_CANDIDATE"
    if _active(row, "TRANSPORT_NETWORK_DISRUPTION"):
        assets = _json_list(row.get("transport_asset_hits", "[]"))
        disruptions = _json_list(row.get("transport_disruption_hits", "[]"))
        excluded = _terms(text, TRANSPORT_EXCLUSIONS)
        hits.extend(f"TRANSPORT_ASSET:{item}" for item in assets)
        hits.extend(f"TRANSPORT_DISRUPTION:{item}" for item in disruptions)
        if excluded:
            transport_tier = "DEFERRED"
            exclusions.append("BACKGROUND_MENTION")
        elif assets and disruptions and geo == "STRONG":
            transport_tier = "HIGH_CONFIDENCE"
        elif assets and disruptions:
            transport_tier = "SECONDARY"
            exclusions.append("ACTOR_GEO_ONLY" if row.get("geo_match_basis") == "ACTOR_GEO" else "WRONG_GEOGRAPHY")
        elif assets:
            transport_tier = "DEFERRED"
            exclusions.append("TRANSPORT_CONTEXT_ONLY")
        else:
            transport_tier = "DEFERRED"
            exclusions.append("DISRUPTION_WITHOUT_ASSET")

    family_tiers = (flash_tier, regional_tier, transport_tier)
    tier = "HIGH_CONFIDENCE" if "HIGH_CONFIDENCE" in family_tiers else "SECONDARY" if "SECONDARY" in family_tiers else "DEFERRED"
    reason = "EXPLICIT_EVIDENCE_AND_CREDIBLE_GEOGRAPHY" if tier == "HIGH_CONFIDENCE" else "PLAUSIBLE_BUT_AMBIGUOUS" if tier == "SECONDARY" else "WEAK_OR_CONTEXTUAL_ONLY"
    return {**row, "candidate_tier": tier, "flash_refinement_tier": flash_tier,
            "regional_refinement_tier": regional_tier, "transport_refinement_tier": transport_tier,
            "anchor_confidence": "", "refinement_reason": reason,
            "refinement_rule_hits": _dump(hits), "refinement_exclusion_flags": _dump(exclusions)}


def refine_anchor(row: dict[str, str]) -> dict[str, str]:
    """Assign source-native anchors HIGH/MEDIUM confidence without keyword-only treatment."""
    source = row.get("source", "")
    text = row.get("title_text_excerpt", "")
    geo = row.get("geo_evidence_strength", "")
    metadata = _json_list(row.get("structured_metadata_hits", "[]"))
    preparedness = bool(_terms(text, PREPAREDNESS))
    meaningful = bool(text.strip() or metadata)
    confidence = "HIGH"
    exclusions: list[str] = []
    if not meaningful:
        confidence = "MEDIUM"; exclusions.append("WEAK_EVENT_INFORMATION")
    if geo != "STRONG":
        confidence = "MEDIUM"; exclusions.append("WRONG_GEOGRAPHY")
    if source == "COPERNICUS_EMS" and preparedness:
        confidence = "MEDIUM"; exclusions.append("PREPAREDNESS_ONLY")

    family_tiers = {family: "NOT_CANDIDATE" for family in FAMILIES}
    for family in FAMILIES:
        if _active(row, family):
            family_tiers[family] = "HIGH_CONFIDENCE" if confidence == "HIGH" else "SECONDARY"
    if _active(row, "TRANSPORT_NETWORK_DISRUPTION"):
        native = "SOURCE_NATIVE_TRANSPORT_IMPACT" in metadata
        assets = _json_list(row.get("transport_asset_hits", "[]"))
        disruptions = _json_list(row.get("transport_disruption_hits", "[]"))
        if not native and not (assets and disruptions):
            family_tiers["TRANSPORT_NETWORK_DISRUPTION"] = "SECONDARY"
            exclusions.append("TRANSPORT_CONTEXT_ONLY")
    tier = "HIGH_CONFIDENCE" if confidence == "HIGH" else "SECONDARY"
    hits = [f"ANCHOR_SOURCE:{source}"] + metadata
    return {**row, "candidate_tier": tier,
            "flash_refinement_tier": family_tiers["FLASH_DEMAND_SURGE"],
            "regional_refinement_tier": family_tiers["REGIONAL_EMERGENCY_DISRUPTION"],
            "transport_refinement_tier": family_tiers["TRANSPORT_NETWORK_DISRUPTION"],
            "anchor_confidence": confidence,
            "refinement_reason": "SOURCE_NATIVE_EVENT_ANCHOR_HIGH" if confidence == "HIGH" else "SOURCE_NATIVE_EVENT_ANCHOR_MEDIUM",
            "refinement_rule_hits": _dump(hits), "refinement_exclusion_flags": _dump(exclusions)}


@contextmanager
def _writer(path: Path, fields: list[str]) -> Iterator[csv.DictWriter]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                writer = csv.DictWriter(text, fieldnames=fields, delimiter="\t", lineterminator="\n", extrasaction="ignore")
                writer.writeheader()
                yield writer


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_candidate_refinement(root: Path) -> dict[str, Any]:
    candidates, audit = root / "candidates", root / "audit"
    inputs = [candidates / name for name in (
        "gdelt_risk_candidates.tsv.gz", "reliefweb_report_risk_candidates.tsv.gz",
        "structured_event_anchors.tsv.gz", "all_risk_candidate_index.tsv.gz",
        "risk_candidate_rule_hits.tsv.gz",
    )]
    before = {str(path.resolve()): file_sha256(path) for path in inputs}
    stage5b2 = json.loads((audit / "stage5b2_candidate_summary.json").read_text(encoding="utf-8"))
    standardized_before = stage5b2["input_sha256"]
    if any(file_sha256(Path(path)) != digest for path, digest in standardized_before.items()):
        raise RuntimeError("A standardized Stage 5B-2 input changed before refinement")

    outputs = {
        "gdelt": candidates / "gdelt_risk_candidates_refined.tsv.gz",
        "reports": candidates / "reliefweb_report_risk_candidates_refined.tsv.gz",
        "anchors": candidates / "structured_event_anchors_refined.tsv.gz",
        "high": candidates / "high_confidence_candidate_index.tsv.gz",
        "secondary": candidates / "secondary_candidate_index.tsv.gz",
        "deferred": candidates / "deferred_candidate_index.tsv.gz",
        "hits": candidates / "stage5b2r_refinement_rule_hits.tsv.gz",
    }
    source_files = [(inputs[0], outputs["gdelt"], refine_candidate), (inputs[1], outputs["reports"], refine_candidate), (inputs[2], outputs["anchors"], refine_anchor)]
    source_rows: list[dict[str, str]] = []
    for input_path, output_path, refiner in source_files:
        with gzip.open(input_path, "rt", encoding="utf-8", newline="") as stream:
            fields = list(csv.DictReader(stream, delimiter="\t").fieldnames or [])
        with _writer(output_path, fields + REFINEMENT_FIELDS) as writer:
            for row in read_tsv(input_path):
                refined = refiner(row); writer.writerow(refined); source_rows.append(refined)

    if len(source_rows) != sum(1 for _ in read_tsv(inputs[3])):
        raise RuntimeError("Refinement lost or added Stage 5B-2 candidates")
    keys = [(row["source"], row["source_record_type"], row["source_record_id"]) for row in source_rows]
    if len(keys) != len(set(keys)):
        raise RuntimeError("Candidate identities are not unique")

    common_fields = list(next(iter(read_tsv(inputs[3]))).keys()) + REFINEMENT_FIELDS
    hit_fields = ["source", "source_record_type", "source_record_id", "risk_family", "hit_type", "rule_hit"]
    writers = {}
    contexts = []
    try:
        for tier, name in zip(TIERS, ("high", "secondary", "deferred")):
            context = _writer(outputs[name], common_fields); contexts.append(context); writers[tier] = context.__enter__()
        hit_context = _writer(outputs["hits"], hit_fields); contexts.append(hit_context); hit_writer = hit_context.__enter__()
        for row in source_rows:
            writers[row["candidate_tier"]].writerow(row)
            for family, field in (("FLASH_DEMAND_SURGE", "flash_refinement_tier"), ("REGIONAL_EMERGENCY_DISRUPTION", "regional_refinement_tier"), ("TRANSPORT_NETWORK_DISRUPTION", "transport_refinement_tier")):
                if row[field] == "NOT_CANDIDATE": continue
                for hit in _json_list(row["refinement_rule_hits"]):
                    hit_writer.writerow({"source": row["source"], "source_record_type": row["source_record_type"], "source_record_id": row["source_record_id"], "risk_family": family, "hit_type": "SUPPORT", "rule_hit": hit})
                for flag in _json_list(row["refinement_exclusion_flags"]):
                    hit_writer.writerow({"source": row["source"], "source_record_type": row["source_record_type"], "source_record_id": row["source_record_id"], "risk_family": family, "hit_type": "EXCLUSION", "rule_hit": flag})
    finally:
        for context in reversed(contexts):
            context.__exit__(None, None, None)

    tier_counts = Counter(row["candidate_tier"] for row in source_rows)
    family_counts = Counter()
    source_counts = Counter()
    exclusions = Counter()
    for row in source_rows:
        source_counts[(row["source"], row["source_record_type"], row["candidate_tier"])] += 1
        exclusions.update(_json_list(row["refinement_exclusion_flags"]))
        for family, field in (("FLASH_DEMAND_SURGE", "flash_refinement_tier"), ("REGIONAL_EMERGENCY_DISRUPTION", "regional_refinement_tier"), ("TRANSPORT_NETWORK_DISRUPTION", "transport_refinement_tier")):
            if row[field] != "NOT_CANDIDATE": family_counts[(family, row[field])] += 1

    family_rows = [{"risk_family": family, "candidate_tier": tier, "count": family_counts.get((family, tier), 0)} for family in FAMILIES for tier in TIERS]
    source_rows_audit = [{"source": source, "source_record_type": typ, "candidate_tier": tier, "count": source_counts.get((source, typ, tier), 0)} for source, typ in sorted({(row["source"], row["source_record_type"]) for row in source_rows}) for tier in TIERS]
    exclusion_rows = [{"exclusion_reason": reason, "count": count} for reason, count in sorted(exclusions.items(), key=lambda item: (-item[1], item[0]))]
    _write_csv(audit / "stage5b2r_family_tier_counts.csv", family_rows, ["risk_family", "candidate_tier", "count"])
    _write_csv(audit / "stage5b2r_source_tier_counts.csv", source_rows_audit, ["source", "source_record_type", "candidate_tier", "count"])
    _write_csv(audit / "stage5b2r_exclusion_reason_counts.csv", exclusion_rows, ["exclusion_reason", "count"])

    sample: list[dict[str, str]] = []
    bins: dict[tuple[str, str], list[tuple[str, dict[str, str]]]] = defaultdict(list)
    for row in source_rows:
        if row["candidate_tier"] != "HIGH_CONFIDENCE": continue
        for family, field in (("FLASH_DEMAND_SURGE", "flash_refinement_tier"), ("REGIONAL_EMERGENCY_DISRUPTION", "regional_refinement_tier"), ("TRANSPORT_NETWORK_DISRUPTION", "transport_refinement_tier")):
            if row[field] == "HIGH_CONFIDENCE":
                digest = sha256(f"{family}|{row['source']}|{row['source_record_type']}|{row['source_record_id']}".encode()).hexdigest()
                bins[(family, row["source"])].append((digest, row))
    selected: set[tuple[str, str, str, str]] = set()
    for (family, source), entries in sorted(bins.items()):
        for _, row in sorted(entries)[:15]: selected.add((family, source, row["source_record_type"], row["source_record_id"]))
    if len(selected) < 150:
        all_options = sorted((sha256(f"{family}|{row['source']}|{row['source_record_type']}|{row['source_record_id']}".encode()).hexdigest(), family, row) for (family, source), entries in bins.items() for _, row in entries)
        for _, family, row in all_options:
            selected.add((family, row["source"], row["source_record_type"], row["source_record_id"]))
            if len(selected) >= 150: break
    row_map = {(row["source"], row["source_record_type"], row["source_record_id"]): row for row in source_rows}
    for family, source, typ, record_id in sorted(selected)[:200]:
        row = row_map[(source, typ, record_id)]
        sample.append({"risk_family": family, "source": source, "source_record_type": typ, "source_record_id": record_id,
            "candidate_tier": row["candidate_tier"], "family_tier": row[{"FLASH_DEMAND_SURGE":"flash_refinement_tier","REGIONAL_EMERGENCY_DISRUPTION":"regional_refinement_tier","TRANSPORT_NETWORK_DISRUPTION":"transport_refinement_tier"}[family]],
            "country_iso3": row["country_iso3"], "geo_match_basis": row["geo_match_basis"], "geo_evidence_strength": row["geo_evidence_strength"],
            "title_text_excerpt": row["title_text_excerpt"], "refinement_rule_hits": row["refinement_rule_hits"], "refinement_exclusion_flags": row["refinement_exclusion_flags"], "source_url": row["source_url"]})
    sample_fields = ["risk_family", "source", "source_record_type", "source_record_id", "candidate_tier", "family_tier", "country_iso3", "geo_match_basis", "geo_evidence_strength", "title_text_excerpt", "refinement_rule_hits", "refinement_exclusion_flags", "source_url"]
    _write_csv(audit / "stage5b2r_high_confidence_audit_sample.csv", sample, sample_fields)

    current = {path: file_sha256(Path(path)) for path in before}
    standardized_after = {path: file_sha256(Path(path)) for path in standardized_before}
    if current != before or standardized_after != standardized_before:
        raise RuntimeError("A Stage 5B-2 or standardized input changed during refinement")
    summary = {"original_candidate_total": len(source_rows), "tier_counts": dict(tier_counts),
        "family_tier_counts": family_rows, "source_tier_counts": source_rows_audit,
        "exclusion_reason_counts": exclusion_rows, "validation_sample_size": len(sample),
        "stage5b2_input_sha_unchanged": True, "standardized_input_sha_unchanged": True,
        "stage5b2_input_sha256": before, "standardized_input_sha256": standardized_before,
        "outputs": {name: str(path.resolve()) for name, path in outputs.items()},
        "output_sha256": {name: file_sha256(path) for name, path in outputs.items()}}
    _write_csv(audit / "stage5b2r_summary.csv", [{"metric": "ORIGINAL_TOTAL", "value": len(source_rows)}] + [{"metric": tier, "value": tier_counts[tier]} for tier in TIERS], ["metric", "value"])
    _write_json(audit / "stage5b2r_summary.json", summary)
    _write_report(audit / "stage5b2r_report.md", summary)
    return summary


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = ["# Stage 5B-2R high-confidence candidate refinement", "",
        "This deterministic refinement ranks every Stage 5B-2 candidate as HIGH_CONFIDENCE, SECONDARY, or DEFERRED. No candidate is deleted. The tiers are screening decisions, not final risk types, severity, probability, calibrated confidence, event grouping, or cross-source linkage.", "",
        "## Rules", "",
        "Flash HIGH_CONFIDENCE requires an explicit abnormal demand or purchasing increase, consumer/emergency-goods context, and credible target geography. Shortage or hoarding alone is insufficient. Regional HIGH_CONFIDENCE requires a specific hazard and strong source/event geography; generic emergency language and ActorGeo-only context are insufficient. Transport HIGH_CONFIDENCE requires an asset plus an actual disruption state, or explicit source-native transport-impact metadata, with credible geography. Structured event anchors are assessed from source-native status rather than as ordinary keyword records.", "",
        "## Overall tiers", ""]
    lines += [f"- {tier}: {summary['tier_counts'].get(tier, 0):,}" for tier in TIERS]
    lines += ["", "## Family tiers", "", "| Family | Tier | Count |", "|---|---|---:|"]
    lines += [f"| {row['risk_family']} | {row['candidate_tier']} | {row['count']:,} |" for row in summary["family_tier_counts"]]
    lines += ["", "## Source tiers", "", "| Source | Type | Tier | Count |", "|---|---|---|---:|"]
    lines += [f"| {row['source']} | {row['source_record_type']} | {row['candidate_tier']} | {row['count']:,} |" for row in summary["source_tier_counts"]]
    lines += ["", "## Main downgrade/exclusion flags", ""]
    lines += [f"- {row['exclusion_reason']}: {row['count']:,}" for row in summary["exclusion_reason_counts"]]
    lines += ["", f"High-confidence validation sample: {summary['validation_sample_size']:,} stratified family-source rows.", "",
        "Stage 5B-2 candidate inputs and all standardized inputs retained their recorded SHA-256 values. Outputs use deterministic ordering and gzip timestamps. No GenAI, GKG, clustering, cross-source linkage, canonical event creation, severity/impact mapping, scenario generation, or optimization was performed.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
