"""Stage 5B-2F deterministic Flash-demand evidence recovery."""

from __future__ import annotations

from collections import Counter
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


STATUSES = ("TRUE_FLASH_EVIDENCE", "PLAUSIBLE_BUT_INSUFFICIENT", "FALSE_POSITIVE")
DIRECT_DEMAND = (
    "demand surge", "surge in demand", "demand spike", "sharp increase in demand",
    "increased demand", "surge in purchases", "increase in purchases", "panic buying",
    "rush to buy", "buying surge", "shopping surge", "sales spike", "increased sales",
    "emergency purchasing", "extraordinary demand", "aumento de la demanda",
    "incremento de la demanda", "pico de demanda", "fuerte aumento de la demanda",
    "compras de pánico", "aumento de compras", "incremento de compras", "aumento de ventas",
    "incremento de ventas", "compra masiva", "demanda extraordinaria",
)
PURCHASING_BEHAVIOR = (
    "surge in purchases", "increase in purchases", "panic buying", "rush to buy",
    "buying surge", "shopping surge", "compras de pánico", "aumento de compras",
    "incremento de compras", "compra masiva",
)
GOODS = (
    "food", "water", "medicine", "medicines", "fuel", "groceries", "supermarket", "retail",
    "essential goods", "household goods", "emergency supplies", "alimentos", "agua",
    "medicamentos", "combustible", "supermercado", "bienes esenciales", "suministros de emergencia",
)
SHORTAGE = (
    "shortage", "scarcity", "stockout", "out of stock", "supply shortage", "logistics shortage",
    "fuel shortage", "production shortage", "import shortage", "escasez", "agotado",
    "desabastecimiento",
)
GENERIC_HOARDING = ("hoarding", "stockpiling", "acaparamiento")
FALSE_PATTERNS = {
    "WEAPONS_OR_MILITARY_STOCKPILING": ("weapons stockpiling", "military stockpiling", "ammunition stockpiling"),
    "SPY_GEAR_STOCKPILING": ("spy gear", "hacking tools"),
    "LAND_HOARDING": ("land hoarding",),
    "FINANCIAL_HOARDING": ("financial hoarding",),
    "STRATEGIC_OR_GOVERNMENT_RESERVE": ("strategic reserve", "government reserve"),
    "VACCINE_STOCKPILING_WITHOUT_DEMAND": ("vaccine stockpiling", "stockpiling vaccine", "stockpiling vaccines"),
    "SUPPLY_SIDE_SHORTAGE": ("supply shortage", "production shortage", "import shortage", "logistics shortage"),
    "TRANSPORT_CAUSED_SHORTAGE": ("transport disruption", "road closure", "route blocked", "port closure"),
}


def _norm(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "")).lower()


def _find(text: str, terms: Iterable[str]) -> list[str]:
    normalized = re.sub(r"[-_/+%]+", " ", _norm(text))
    return sorted({term for term in terms if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", normalized)})


def _nearby_goods(text: str, direct: Iterable[str], radius: int = 80) -> list[str]:
    """Return goods terms near a direct phrase; whole-document co-occurrence is insufficient."""
    normalized = re.sub(r"[-_/+%]+", " ", _norm(text))
    nearby: set[str] = set()
    for phrase in direct:
        for match in re.finditer(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized):
            window = normalized[max(0, match.start() - radius):match.end() + radius]
            nearby.update(_find(window, GOODS))
    return sorted(nearby)


def _dump(values: Iterable[str]) -> str:
    return json.dumps(sorted(set(value for value in values if value)), ensure_ascii=False, separators=(",", ":"))


def _false_reason(text: str, direct: list[str]) -> str:
    if direct:
        return ""
    for reason, terms in FALSE_PATTERNS.items():
        if _find(text, terms):
            return reason
    if _find(text, GENERIC_HOARDING):
        return "GENERIC_HOARDING_OR_STOCKPILING"
    return ""


def classify_flash_evidence(text: str, geo_status: str, *, source: str) -> dict[str, Any]:
    """Classify only explicit evidence available in supplied local source fields."""
    direct = _find(text, DIRECT_DEMAND)
    goods = _nearby_goods(text, direct)
    behavior = _find(text, PURCHASING_BEHAVIOR)
    shortage = _find(text, SHORTAGE)
    false_reason = _false_reason(text, direct)
    geo_valid = geo_status == "STRONG"

    if false_reason:
        status = "FALSE_POSITIVE"
        basis = "EXPLICIT_FALSE_POSITIVE_CONTEXT"
    elif direct and (goods or behavior) and geo_valid:
        status = "TRUE_FLASH_EVIDENCE"
        basis = "DIRECT_DEMAND_PHRASE_WITH_GOODS_CONTEXT" if goods else "EXPLICIT_ABNORMAL_PURCHASING_BEHAVIOR"
    elif direct:
        status = "PLAUSIBLE_BUT_INSUFFICIENT"
        basis = "DIRECT_PHRASE_WITH_INSUFFICIENT_CONTEXT" if geo_valid else "DIRECT_PHRASE_WITH_INSUFFICIENT_GEOGRAPHY"
    elif shortage:
        status = "PLAUSIBLE_BUT_INSUFFICIENT"
        basis = "SHORTAGE_WITHOUT_DIRECT_DEMAND_INCREASE"
    else:
        status = "FALSE_POSITIVE"
        basis = "NO_DIRECT_DEMAND_INCREASE_EVIDENCE"
        false_reason = "WEAK_OR_AMBIGUOUS_KEYWORD"

    needs_gkg = source == "GDELT" and status == "PLAUSIBLE_BUT_INSUFFICIENT" and geo_status in {"STRONG", "MEDIUM"}
    return {"flash_evidence_status": status, "flash_evidence_basis": basis,
            "flash_evidence_phrases": direct, "flash_goods_context": goods,
            "flash_false_positive_reason": false_reason,
            "needs_gkg_enrichment": needs_gkg}


def reliefweb_evidence_text(row: dict[str, str]) -> str:
    return " ".join(row.get(name, "") for name in (
        "title", "body_text", "theme_names", "disaster_names", "disaster_type_names",
        "country_names", "primary_country_name",
    ))


def gdelt_evidence_text(row: dict[str, str]) -> str:
    return " ".join(row.get(name, "") for name in (
        "Actor1Name", "Actor2Name", "Actor1Geo_FullName", "Actor2Geo_FullName",
        "ActionGeo_FullName", "title_raw", "description_raw", "SOURCEURL", "source_url",
    ))


@contextmanager
def _writer(path: Path, fields: list[str]) -> Iterator[csv.DictWriter]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                writer = csv.DictWriter(text, fieldnames=fields, delimiter="\t", lineterminator="\n", extrasaction="ignore")
                writer.writeheader(); yield writer


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _recorded_input_paths(root: Path) -> list[Path]:
    audit = root / "audit"
    stage2 = json.loads((audit / "stage5b2_candidate_summary.json").read_text(encoding="utf-8"))
    stage2r = json.loads((audit / "stage5b2r_summary.json").read_text(encoding="utf-8"))
    paths = [Path(path) for path in stage2["input_sha256"]]
    paths += [Path(path) for path in stage2r["stage5b2_input_sha256"]]
    paths += [Path(path) for path in stage2r["outputs"].values()]
    return sorted(set(paths), key=lambda path: str(path))


def run_flash_evidence_recovery(root: Path) -> dict[str, Any]:
    candidates, processed, audit, targets = root / "candidates", root / "processed", root / "audit", root / "gkg_targets"
    protected = _recorded_input_paths(root)
    protected_before = {str(path.resolve()): file_sha256(path) for path in protected}
    explicit_inputs = [processed / name for name in (
        "gdelt_event_standardized.tsv.gz", "reliefweb_reports_standardized.tsv.gz",
        "reliefweb_report_disaster_links.tsv.gz",
    )]
    explicit_before = {str(path.resolve()): file_sha256(path) for path in explicit_inputs}

    flash_rows: dict[tuple[str, str], dict[str, str]] = {}
    for filename in ("gdelt_risk_candidates_refined.tsv.gz", "reliefweb_report_risk_candidates_refined.tsv.gz"):
        for row in read_tsv(candidates / filename):
            if row.get("candidate_flash_demand", "").lower() == "true":
                key = (row["source"], row["source_record_id"])
                if key in flash_rows: raise RuntimeError(f"Duplicate Flash candidate: {key}")
                flash_rows[key] = row
    if not flash_rows:
        raise RuntimeError("No Flash candidates found")

    gdelt_ids = {record_id for source, record_id in flash_rows if source == "GDELT"}
    report_ids = {record_id for source, record_id in flash_rows if source == "RELIEFWEB"}
    gdelt_native = {}
    for row in read_tsv(processed / "gdelt_event_standardized.tsv.gz"):
        if row.get("source_record_id") in gdelt_ids:
            gdelt_native[row["source_record_id"]] = row
    report_native = {}
    for row in read_tsv(processed / "reliefweb_reports_standardized.tsv.gz"):
        if row.get("source_record_id") in report_ids:
            report_native[row["source_record_id"]] = row
    if gdelt_ids != set(gdelt_native) or report_ids != set(report_native):
        raise RuntimeError("A Flash candidate cannot be joined to its standardized source record")

    review_rows: list[dict[str, Any]] = []
    relief_rows: list[dict[str, Any]] = []
    gdelt_rows: list[dict[str, Any]] = []
    recovered_full_body = 0
    for (source, record_id), candidate in sorted(flash_rows.items()):
        current_flash_tier = candidate.get("flash_refinement_tier", "")
        if source == "RELIEFWEB":
            native = report_native[record_id]
            full_text = reliefweb_evidence_text(native)
            result = classify_flash_evidence(full_text, candidate.get("geo_evidence_strength", ""), source=source)
            excerpt_result = classify_flash_evidence(candidate.get("title_text_excerpt", ""), candidate.get("geo_evidence_strength", ""), source=source)
            body_recovered = result["flash_evidence_status"] == "TRUE_FLASH_EVIDENCE" and excerpt_result["flash_evidence_status"] != "TRUE_FLASH_EVIDENCE"
            recovered_full_body += int(body_recovered)
            row = {"source_record_id": record_id, "date_original": native.get("date_original", ""), "title": native.get("title", ""),
                "country_iso3_list": native.get("country_iso3_list", "[]"), "target_country_iso3_list": native.get("target_country_iso3_list", "[]"),
                "candidate_tier": current_flash_tier, "overall_candidate_tier": candidate.get("candidate_tier", ""),
                **{key: _dump(value) if isinstance(value, list) else str(value).lower() if isinstance(value, bool) else value for key, value in result.items()},
                "flash_geo_status": candidate.get("geo_evidence_strength", ""), "native_disaster_link_present": candidate.get("native_disaster_link_present", "false"),
                "source_url": native.get("reliefweb_url", ""), "quality_flags": native.get("quality_flags", ""),
                "full_body_used": "true", "evidence_recovered_beyond_excerpt": str(body_recovered).lower()}
            relief_rows.append(row)
        else:
            native = gdelt_native[record_id]
            geo = candidate.get("geo_evidence_strength", "")
            result = classify_flash_evidence(gdelt_evidence_text(native), geo, source=source)
            gdelt_status = {"TRUE_FLASH_EVIDENCE":"GDELT_FLASH_SUPPORTED", "PLAUSIBLE_BUT_INSUFFICIENT":"GDELT_FLASH_INSUFFICIENT", "FALSE_POSITIVE":"GDELT_FLASH_FALSE_POSITIVE"}[result["flash_evidence_status"]]
            row = {"GlobalEventID": native.get("GlobalEventID", record_id), "source_record_id": record_id,
                "event_date": native.get("event_date", ""), "ActionGeo": native.get("ActionGeo_FullName", ""),
                "Actor1Geo": native.get("Actor1Geo_FullName", ""), "Actor2Geo": native.get("Actor2Geo_FullName", ""),
                "ActionGeo_CountryCode": native.get("ActionGeo_CountryCode", ""), "candidate_tier": current_flash_tier,
                "overall_candidate_tier": candidate.get("candidate_tier", ""), "gdelt_flash_status": gdelt_status,
                **{key: _dump(value) if isinstance(value, list) else str(value).lower() if isinstance(value, bool) else value for key, value in result.items()},
                "flash_geo_status": geo, "SOURCEURL": native.get("SOURCEURL", ""), "current_rule_hits": candidate.get("flash_rule_hits", "[]")}
            gdelt_rows.append(row)
        review_rows.append({"source": source, "source_record_id": record_id, "candidate_tier": current_flash_tier,
            "overall_candidate_tier": candidate.get("candidate_tier", ""), "flash_evidence_status": result["flash_evidence_status"],
            "flash_evidence_basis": result["flash_evidence_basis"], "flash_evidence_phrases": _dump(result["flash_evidence_phrases"]),
            "flash_goods_context": _dump(result["flash_goods_context"]), "flash_geo_status": candidate.get("geo_evidence_strength", ""),
            "flash_false_positive_reason": result["flash_false_positive_reason"], "needs_gkg_enrichment": str(result["needs_gkg_enrichment"]).lower()})

    if len(review_rows) != len(flash_rows) or len({(row["source"], row["source_record_id"]) for row in review_rows}) != len(review_rows):
        raise RuntimeError("Flash candidate accounting is not one-to-one")

    relief_fields = ["source_record_id", "date_original", "title", "country_iso3_list", "target_country_iso3_list", "candidate_tier", "overall_candidate_tier", "flash_evidence_status", "flash_evidence_basis", "flash_evidence_phrases", "flash_goods_context", "flash_geo_status", "flash_false_positive_reason", "native_disaster_link_present", "source_url", "quality_flags", "full_body_used", "evidence_recovered_beyond_excerpt", "needs_gkg_enrichment"]
    gdelt_fields = ["GlobalEventID", "source_record_id", "event_date", "ActionGeo", "Actor1Geo", "Actor2Geo", "ActionGeo_CountryCode", "candidate_tier", "overall_candidate_tier", "gdelt_flash_status", "flash_evidence_status", "flash_evidence_basis", "flash_evidence_phrases", "flash_goods_context", "flash_geo_status", "flash_false_positive_reason", "needs_gkg_enrichment", "current_rule_hits", "SOURCEURL"]
    index_fields = ["source", "source_record_id", "candidate_tier", "overall_candidate_tier", "flash_evidence_status", "flash_evidence_basis", "flash_evidence_phrases", "flash_goods_context", "flash_geo_status", "flash_false_positive_reason", "needs_gkg_enrichment"]
    output_paths = {"reliefweb": candidates / "flash_reliefweb_evidence_review.tsv.gz", "gdelt": candidates / "flash_gdelt_evidence_review.tsv.gz", "index": candidates / "flash_candidate_evidence_index.tsv.gz", "gkg_events": targets / "flash_gkg_target_events.tsv.gz"}
    for path, fields, rows in ((output_paths["reliefweb"], relief_fields, relief_rows), (output_paths["gdelt"], gdelt_fields, gdelt_rows), (output_paths["index"], index_fields, review_rows)):
        with _writer(path, fields) as writer: writer.writerows(rows)

    target_rows = [{"GlobalEventID": row["GlobalEventID"], "event_date": row["event_date"], "SOURCEURL": row["SOURCEURL"],
        "ActionGeo_CountryCode": row["ActionGeo_CountryCode"], "ActionGeo_FullName": row["ActionGeo"],
        "current_flash_evidence_status": row["flash_evidence_status"], "current_rule_hits": row["current_rule_hits"],
        "why_gkg_needed": row["flash_evidence_basis"]} for row in gdelt_rows if row["needs_gkg_enrichment"] == "true"]
    target_fields = ["GlobalEventID", "event_date", "SOURCEURL", "ActionGeo_CountryCode", "ActionGeo_FullName", "current_flash_evidence_status", "current_rule_hits", "why_gkg_needed"]
    with _writer(output_paths["gkg_events"], target_fields) as writer: writer.writerows(target_rows)
    date_counts = Counter(row["event_date"] for row in target_rows)
    date_urls: dict[str, set[str]] = {}
    for row in target_rows: date_urls.setdefault(row["event_date"], set()).add(row["SOURCEURL"])
    date_rows = [{"date": day, "target_event_count": count, "unique_source_urls": len(date_urls[day] - {""})} for day, count in sorted(date_counts.items())]
    date_path = targets / "flash_gkg_target_dates.csv"
    _write_csv(date_path, date_rows, ["date", "target_event_count", "unique_source_urls"])

    status_counts = Counter(row["flash_evidence_status"] for row in review_rows)
    source_status = Counter((row["source"], row["flash_evidence_status"]) for row in review_rows)
    source_counts = Counter(row["source"] for row in review_rows)
    tier_counts = Counter(row["candidate_tier"] for row in review_rows)
    false_reasons = Counter(row["flash_false_positive_reason"] for row in review_rows if row["flash_false_positive_reason"])
    upgraded = sum(row["candidate_tier"] != "HIGH_CONFIDENCE" and row["flash_evidence_status"] == "TRUE_FLASH_EVIDENCE" for row in review_rows)
    downgraded = sum(row["candidate_tier"] != "DEFERRED" and row["flash_evidence_status"] == "FALSE_POSITIVE" for row in review_rows)
    unresolved = status_counts["PLAUSIBLE_BUT_INSUFFICIENT"]

    sample_rows: list[dict[str, Any]] = []
    for status in STATUSES:
        group = [row for row in review_rows if row["flash_evidence_status"] == status]
        ranked = sorted(group, key=lambda row: sha256(f"{row['source']}|{row['source_record_id']}|{status}".encode()).hexdigest())
        take = len(ranked) if status == "TRUE_FLASH_EVIDENCE" and len(ranked) < 20 else min(30, len(ranked))
        sample_rows.extend(ranked[:take])
    sample_path = audit / "stage5b2f_flash_audit_sample.csv"
    _write_csv(sample_path, sample_rows, index_fields)
    false_rows = [{"false_positive_reason": reason, "count": count} for reason, count in sorted(false_reasons.items(), key=lambda item: (-item[1], item[0]))]
    _write_csv(audit / "stage5b2f_false_positive_reasons.csv", false_rows, ["false_positive_reason", "count"])
    _write_csv(audit / "stage5b2f_gkg_target_summary.csv", date_rows, ["date", "target_event_count", "unique_source_urls"])

    protected_after = {path: file_sha256(Path(path)) for path in protected_before}
    explicit_after = {path: file_sha256(Path(path)) for path in explicit_before}
    if protected_before != protected_after or explicit_before != explicit_after:
        raise RuntimeError("A Stage 5B-2/2R or standardized input changed")
    summary = {"total_flash_candidates": len(review_rows), "source_counts": dict(source_counts), "current_flash_tier_counts": dict(tier_counts),
        "evidence_status_counts": dict(status_counts), "source_status_counts": {f"{source}|{status}": count for (source, status), count in source_status.items()},
        "false_positive_reasons": false_rows, "gdelt_needs_gkg_enrichment": len(target_rows), "distinct_gkg_target_dates": len(date_rows),
        "reliefweb_full_body_rows_scanned": len(relief_rows), "reliefweb_true_recovered_beyond_excerpt": recovered_full_body,
        "upgraded_relative_to_flash_tier": upgraded, "downgraded_relative_to_flash_tier": downgraded, "unresolved": unresolved,
        "validation_sample_size": len(sample_rows), "protected_input_sha_unchanged": True, "standardized_input_sha_unchanged": True,
        "protected_input_sha256": protected_before, "explicit_input_sha256": explicit_before,
        "outputs": {**{name: str(path.resolve()) for name, path in output_paths.items()}, "gkg_dates": str(date_path.resolve()), "sample": str(sample_path.resolve())}}
    summary_rows = [{"metric": "TOTAL_FLASH_CANDIDATES", "value": len(review_rows)}]
    summary_rows += [{"metric": f"SOURCE_{key}", "value": value} for key, value in sorted(source_counts.items())]
    summary_rows += [{"metric": f"STATUS_{key}", "value": status_counts[key]} for key in STATUSES]
    summary_rows += [{"metric": "GDELT_NEEDS_GKG_ENRICHMENT", "value": len(target_rows)}, {"metric": "DISTINCT_GKG_TARGET_DATES", "value": len(date_rows)}, {"metric": "RELIEFWEB_FULL_BODY_RECOVERED", "value": recovered_full_body}]
    _write_csv(audit / "stage5b2f_flash_summary.csv", summary_rows, ["metric", "value"])
    _write_json(audit / "stage5b2f_flash_summary.json", summary)
    _write_report(audit / "stage5b2f_flash_report.md", summary)
    return summary


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = ["# Stage 5B-2F Flash-demand targeted evidence recovery", "",
        "This deterministic audit re-examines only the Stage 5B-2 Flash candidates. ReliefWeb reports use the complete locally standardized body and source metadata. GDELT uses preserved Event fields and does not assume that an Event record contains article full text. No GKG data was downloaded.", "",
        "## Population", "", f"- Total Flash candidates: {summary['total_flash_candidates']:,}"]
    lines += [f"- {source}: {count:,}" for source, count in sorted(summary["source_counts"].items())]
    lines += ["", "Current Flash-family tiers are read from `flash_refinement_tier`, not the record-wide tier that another risk family may raise.", "", "## Evidence status", ""]
    lines += [f"- {status}: {summary['evidence_status_counts'].get(status, 0):,}" for status in STATUSES]
    lines += ["", "## Source by status", ""] + [f"- {key}: {value:,}" for key, value in sorted(summary["source_status_counts"].items())]
    lines += ["", "## False-positive reasons", ""] + [f"- {row['false_positive_reason']}: {row['count']:,}" for row in summary["false_positive_reasons"]]
    lines += ["", "## Recovery and unresolved evidence", "",
        f"- ReliefWeb full bodies scanned: {summary['reliefweb_full_body_rows_scanned']:,}",
        f"- ReliefWeb TRUE evidence recovered beyond the Stage 5B-2 excerpt: {summary['reliefweb_true_recovered_beyond_excerpt']:,}",
        f"- Records upgraded relative to Flash-family tier: {summary['upgraded_relative_to_flash_tier']:,}",
        f"- Records downgraded relative to Flash-family tier: {summary['downgraded_relative_to_flash_tier']:,}",
        f"- Plausible but unresolved: {summary['unresolved']:,}",
        f"- Eligible GDELT records requiring later targeted GKG enrichment: {summary['gdelt_needs_gkg_enrichment']:,}",
        f"- Distinct target dates: {summary['distinct_gkg_target_dates']:,}", "",
        "TRUE evidence requires a direct demand/purchasing increase plus consumer/emergency-goods context, or explicit abnormal consumer purchasing behavior, and credible target geography. Shortage, hoarding, stockpiling, promotions, holidays, price increases, and supply-side problems do not establish demand surge by themselves.", "",
        "All Stage 5B-2/2R and standardized inputs retained their recorded SHA-256 values. No GenAI, GKG acquisition, clustering, linkage, canonical event construction, severity/impact mapping, scenario generation, or optimization was performed.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
