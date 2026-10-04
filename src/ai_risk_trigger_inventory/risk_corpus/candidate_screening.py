"""Deterministic high-recall Stage 5B-2 risk-candidate screening."""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
import csv
from datetime import date
from functools import lru_cache
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path
import re
from typing import Any, Iterable, Iterator
import unicodedata


ANALYSIS_START = date(2015, 2, 19)
ANALYSIS_END = date(2026, 8, 31)
TARGET_ISO3 = {"ECU", "COL", "PER"}
COUNTRY_CODES = {"EC": "ECU", "ECU": "ECU", "CO": "COL", "COL": "COL", "PE": "PER", "PER": "PER"}

FLASH_DIRECT = (
    "demand surge", "demand spike", "sales spike", "rush to buy", "panic buying",
    "stockpiling", "hoarding", "shopping surge", "buying surge", "consumer demand increased",
    "sharp increase in demand", "emergency purchasing", "essential goods demand",
    "aumento de la demanda", "incremento de la demanda", "pico de demanda",
    "compras de pánico", "compra masiva", "acaparamiento", "aumento de compras",
    "incremento de ventas", "alta demanda", "demanda extraordinaria",
)
FLASH_SHORTAGE = (
    "out of stock", "stockout", "supermarket shortage", "retail shortage", "shortage",
    "escasez en supermercados", "agotado", "desabastecimiento", "escasez",
)
FLASH_PRESSURE_CONTEXT = ("demand", "buying", "purchasing", "sales", "demanda", "compras", "ventas")
FLASH_WEAK = ("promotion", "holiday", "festival", "shopping season", "discount", "campaign")
REGIONAL = (
    "flood", "flash flood", "earthquake", "landslide", "mudslide", "storm", "heavy rain",
    "extreme rainfall", "hurricane", "tropical cyclone", "wildfire", "volcanic eruption",
    "eruption", "drought", "tsunami", "epidemic", "outbreak", "public health emergency",
    "evacuation", "state of emergency", "natural disaster", "disaster", "emergency declaration",
    "inundación", "inundaciones", "crecida", "desbordamiento", "terremoto", "sismo",
    "deslizamiento", "huaico", "lluvias intensas", "lluvia extrema", "tormenta", "huracán",
    "ciclón", "incendio forestal", "erupción", "volcán", "sequía", "epidemia", "brote",
    "emergencia sanitaria", "evacuación", "estado de emergencia", "desastre", "emergencia",
)
TRANSPORT_ASSET = (
    "road", "highway", "bridge", "rail", "railway", "port", "airport", "terminal",
    "logistics", "route", "corridor", "border crossing", "freight", "transport",
    "transportation", "supply route", "access road", "carretera", "vía", "autopista",
    "puente", "ferrocarril", "puerto", "aeropuerto", "terminal", "logística", "ruta",
    "corredor", "paso fronterizo", "transporte", "acceso vial",
)
TRANSPORT_DISRUPTION = (
    "closed", "closure", "blocked", "blockade", "shutdown", "disrupted", "unavailable",
    "damaged", "destroyed", "impassable", "suspended", "halted", "cut off", "interrupted",
    "collapsed", "restricted access", "cerrado", "cierre", "bloqueado", "bloqueo",
    "interrumpido", "interrupción", "dañado", "destruido", "intransitable", "suspendido",
    "colapsado", "sin acceso", "restricción de acceso", "corte de vía",
)
CAMEO_REGIONAL = {"023", "070", "071", "072", "073", "074", "075"}
CAMEO_TRANSPORT = {"144"}

COMMON_FIELDS = [
    "source", "source_record_type", "source_record_id", "event_date", "country_iso3",
    "candidate_flash_demand", "candidate_regional_emergency", "candidate_transport_network",
    "flash_rule_hits", "regional_rule_hits", "transport_asset_hits", "transport_disruption_hits",
    "structured_metadata_hits", "cameo_support_hits", "geo_match_basis", "geo_evidence_strength",
    "screening_score", "screening_priority", "quality_flags", "source_url", "source_file",
    "evidence_role", "title_text_excerpt",
]
GDELT_EXTRA = [
    "ActionGeo", "Actor1Geo", "Actor2Geo", "ActionGeo_CountryCode", "ActionGeo_ADM1Code",
    "Actor1Geo_CountryCode", "Actor1Geo_ADM1Code", "Actor2Geo_CountryCode", "Actor2Geo_ADM1Code",
    "EventCode", "EventBaseCode", "EventRootCode", "QuadClass", "GoldsteinScale", "NumMentions",
    "NumSources", "NumArticles", "AvgTone", "SOURCEURL", "in_analysis_window",
]
REPORT_EXTRA = ["native_disaster_link_present", "linked_disaster_ids", "evidence_role"]


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _norm(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "")).lower()


@lru_cache(maxsize=None)
def _term_pattern(terms: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(term) for term in sorted(terms, key=lambda item: (-len(item), item)))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")


def find_terms(text: str, terms: Iterable[str]) -> list[str]:
    normalized = re.sub(r"[-_/+%]+", " ", _norm(text))
    return sorted(set(match.group(0) for match in _term_pattern(tuple(terms)).finditer(normalized)))


def lexical_signals(text: str) -> dict[str, list[str] | bool]:
    direct = find_terms(text, FLASH_DIRECT)
    shortage = find_terms(text, FLASH_SHORTAGE)
    pressure = find_terms(text, FLASH_PRESSURE_CONTEXT)
    flash = direct + ([f"SHORTAGE_WITH_PRESSURE:{term}" for term in shortage] if pressure else [])
    regional = find_terms(text, REGIONAL)
    assets = find_terms(text, TRANSPORT_ASSET)
    disruptions = find_terms(text, TRANSPORT_DISRUPTION)
    return {
        "flash": sorted(set(flash)), "flash_weak": find_terms(text, FLASH_WEAK),
        "shortage_only": bool(shortage and not pressure and not direct),
        "regional": sorted(set(regional)), "assets": sorted(set(assets)),
        "disruptions": sorted(set(disruptions)),
        "transport": bool(assets and disruptions),
    }


def gdelt_geo(row: dict[str, str], text: str = "") -> tuple[str, str, str]:
    action = COUNTRY_CODES.get(row.get("ActionGeo_CountryCode", "").upper())
    actor_hits = sorted({COUNTRY_CODES.get(row.get(name, "").upper()) for name in ("Actor1Geo_CountryCode", "Actor2Geo_CountryCode")} - {None})
    if action:
        return "ACTION_GEO", "STRONG", action
    if actor_hits:
        return "ACTOR_GEO", "MEDIUM", actor_hits[0]
    weak = []
    lower = _norm(text)
    for iso3, names in {"ECU": ("ecuador",), "COL": ("colombia",), "PER": ("peru", "perú")}.items():
        if any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", lower) for name in names):
            weak.append(iso3)
    if weak:
        return "TEXT_OR_URL", "WEAK", sorted(weak)[0]
    return "NONE", "", row.get("country_iso3", "") if row.get("country_iso3", "") in TARGET_ISO3 else ""


def _j(values: Iterable[str]) -> str:
    return json.dumps(sorted(set(v for v in values if v)), ensure_ascii=False, separators=(",", ":"))


def _bool(value: bool) -> str:
    return str(bool(value)).lower()


def _priority(role: str, flash: bool, regional: bool, transport: bool, geo: str, native_link: bool = False) -> str:
    families = sum((flash, regional, transport))
    if role == "EVENT_ANCHOR":
        return "A"
    if families >= 2 or (geo == "STRONG" and role == "EVENT_RECORD_CANDIDATE" and (regional or transport)) or (native_link and families):
        return "A"
    if families and geo == "STRONG":
        return "B"
    return "C"


def _score(role: str, families: int, geo: str, structured: int, cameo: int, native: bool) -> int:
    return (6 if role == "EVENT_ANCHOR" else 0) + 4 * families + {"STRONG": 3, "MEDIUM": 2, "WEAK": 1}.get(geo, 0) + min(structured, 3) * 2 + min(cameo, 2) + (2 if native else 0)


@contextmanager
def gzip_writer(path: Path, fields: list[str]) -> Iterator[csv.DictWriter]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                writer = csv.DictWriter(text, fieldnames=fields, delimiter="\t", lineterminator="\n", extrasaction="ignore")
                writer.writeheader()
                yield writer


def read_tsv(path: Path) -> Iterator[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        csv.field_size_limit(2_147_483_647)
        yield from csv.DictReader(stream, delimiter="\t")


def _candidate(row: dict[str, str], *, source: str, record_type: str, role: str, text: str,
               structured: list[str] | None = None, geo_basis: str = "SOURCE_NATIVE_COUNTRY",
               geo_strength: str = "STRONG", native_link: bool = False,
               force_regional: bool = False, force_transport: bool = False) -> dict[str, Any]:
    sig = lexical_signals(text)
    flash = bool(sig["flash"])
    regional = force_regional or bool(sig["regional"])
    transport = force_transport or bool(sig["transport"])
    families = sum((flash, regional, transport))
    metadata = structured or []
    priority = _priority(role, flash, regional, transport, geo_strength, native_link)
    return {
        "source": source, "source_record_type": record_type,
        "source_record_id": row.get("source_record_id", ""),
        "event_date": row.get("event_date", row.get("date_original", "")),
        "country_iso3": row.get("country_iso3", row.get("primary_country_iso3", "")),
        "candidate_flash_demand": _bool(flash), "candidate_regional_emergency": _bool(regional),
        "candidate_transport_network": _bool(transport), "flash_rule_hits": _j(sig["flash"]),
        "regional_rule_hits": _j(sig["regional"]), "transport_asset_hits": _j(sig["assets"]),
        "transport_disruption_hits": _j(sig["disruptions"]), "structured_metadata_hits": _j(metadata),
        "cameo_support_hits": "[]", "geo_match_basis": geo_basis, "geo_evidence_strength": geo_strength,
        "screening_score": _score(role, families, geo_strength, len(metadata), 0, native_link),
        "screening_priority": priority, "quality_flags": row.get("quality_flags", row.get("quality_issues", "")),
        "source_url": row.get("source_url", row.get("reliefweb_url", "")),
        "source_file": row.get("source_file", row.get("source_files", "")), "evidence_role": role,
        "title_text_excerpt": re.sub(r"\s+", " ", text).strip()[:500],
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = fields or sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=names, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_candidate_screening(input_root: Path) -> dict[str, Any]:
    processed = input_root / "processed"; candidates = input_root / "candidates"; audit = input_root / "audit"
    inputs = [processed / name for name in (
        "gdelt_event_standardized.tsv.gz", "usgs_standardized.tsv.gz", "gdacs_standardized.tsv.gz",
        "desinventar_standardized.tsv.gz", "copernicus_ems_standardized.tsv.gz",
        "reliefweb_disasters_standardized.tsv.gz", "reliefweb_reports_standardized.tsv.gz",
        "reliefweb_report_disaster_links.tsv.gz", "reliefweb_report_index.tsv.gz",
        "common_event_index.tsv.gz", "common_event_index_reliefweb_addition.tsv.gz",
    )]
    before = {str(path.resolve()): file_sha256(path) for path in inputs}
    output_paths = {
        "gdelt": candidates / "gdelt_risk_candidates.tsv.gz",
        "reports": candidates / "reliefweb_report_risk_candidates.tsv.gz",
        "anchors": candidates / "structured_event_anchors.tsv.gz",
        "index": candidates / "all_risk_candidate_index.tsv.gz",
        "hits": candidates / "risk_candidate_rule_hits.tsv.gz",
    }
    counts = Counter(); family = Counter(); priority = Counter(); countries = Counter(); geo = Counter(); rules = Counter(); sources = Counter(); source_family = Counter(); overlaps = Counter()
    sample_bins: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = defaultdict(list)

    def observe(candidate: dict[str, Any], hit_writer: csv.DictWriter) -> None:
        flags = {"FLASH_DEMAND_SURGE": candidate["candidate_flash_demand"] == "true",
                 "REGIONAL_EMERGENCY_DISRUPTION": candidate["candidate_regional_emergency"] == "true",
                 "TRANSPORT_NETWORK_DISRUPTION": candidate["candidate_transport_network"] == "true"}
        active = tuple(name for name, enabled in flags.items() if enabled)
        priority[candidate["screening_priority"]] += 1; countries[candidate["country_iso3"] or "UNKNOWN"] += 1
        geo[(candidate["geo_match_basis"], candidate["geo_evidence_strength"] or "NONE")] += 1
        sources[(candidate["source"], candidate["source_record_type"])] += 1; overlaps["+".join(active) or "ANCHOR_ONLY"] += 1
        for fam, enabled in flags.items():
            if enabled:
                family[fam] += 1
                source_family[(candidate["source"], candidate["source_record_type"], fam)] += 1
                key = sha256(f"{candidate['source']}|{candidate['source_record_type']}|{candidate['source_record_id']}|{fam}".encode()).hexdigest()
                bucket = sample_bins[(fam, candidate["screening_priority"])]
                bucket.append((key, candidate.copy())); bucket.sort(key=lambda item: item[0]); del bucket[30:]
        columns = {
            "FLASH_DEMAND_SURGE": "flash_rule_hits", "REGIONAL_EMERGENCY_DISRUPTION": "regional_rule_hits",
            "TRANSPORT_ASSET": "transport_asset_hits", "TRANSPORT_DISRUPTION": "transport_disruption_hits",
            "STRUCTURED_METADATA": "structured_metadata_hits", "CAMEO_SUPPORT": "cameo_support_hits",
        }
        for signal, column in columns.items():
            for hit in json.loads(candidate[column]):
                rules[(signal, hit)] += 1
                hit_writer.writerow({"source": candidate["source"], "source_record_type": candidate["source_record_type"],
                    "source_record_id": candidate["source_record_id"], "signal_family": signal, "rule_hit": hit})

    index_fields = COMMON_FIELDS
    hit_fields = ["source", "source_record_type", "source_record_id", "signal_family", "rule_hit"]
    with gzip_writer(output_paths["gdelt"], COMMON_FIELDS + GDELT_EXTRA) as gw, gzip_writer(output_paths["reports"], COMMON_FIELDS + [f for f in REPORT_EXTRA if f not in COMMON_FIELDS]) as rw, gzip_writer(output_paths["anchors"], COMMON_FIELDS) as aw, gzip_writer(output_paths["index"], index_fields) as iw, gzip_writer(output_paths["hits"], hit_fields) as hw:
        for row in read_tsv(processed / "gdelt_event_standardized.tsv.gz"):
            counts["GDELT_SCREENED"] += 1
            try: parsed = date.fromisoformat(row.get("event_date", "")[:10])
            except ValueError: parsed = None
            in_window = parsed is not None and ANALYSIS_START <= parsed <= ANALYSIS_END
            if in_window: counts["GDELT_IN_WINDOW"] += 1
            text = " ".join(row.get(name, "") for name in ("Actor1Name", "Actor2Name", "Actor1Geo_FullName", "Actor2Geo_FullName", "ActionGeo_FullName", "SOURCEURL"))
            sig = lexical_signals(text); basis, strength, country = gdelt_geo(row, text)
            codes = {row.get("EventCode", ""), row.get("EventBaseCode", ""), row.get("EventRootCode", "")}
            cameo = ([f"REGIONAL:{code}" for code in sorted(codes & CAMEO_REGIONAL)] + [f"TRANSPORT:{code}" for code in sorted(codes & CAMEO_TRANSPORT)])
            flags = (bool(sig["flash"]), bool(sig["regional"]), bool(sig["transport"]))
            if not (in_window and strength and any(flags)): continue
            candidate = _candidate(row, source="GDELT", record_type="EVENT", role="EVENT_RECORD_CANDIDATE", text=text, geo_basis=basis, geo_strength=strength)
            candidate.update({"country_iso3": country, "cameo_support_hits": _j(cameo),
                "screening_score": _score("EVENT_RECORD_CANDIDATE", sum(flags), strength, 0, len(cameo), False),
                "ActionGeo": row.get("ActionGeo_FullName", ""), "Actor1Geo": row.get("Actor1Geo_FullName", ""), "Actor2Geo": row.get("Actor2Geo_FullName", ""),
                **{name: row.get(name, "") for name in GDELT_EXTRA if name not in {"ActionGeo", "Actor1Geo", "Actor2Geo", "in_analysis_window"}},
                "in_analysis_window": "true"})
            gw.writerow(candidate); iw.writerow(candidate); observe(candidate, hw); counts["GDELT_CANDIDATES"] += 1

        for row in read_tsv(processed / "reliefweb_reports_standardized.tsv.gz"):
            counts["RELIEFWEB_REPORTS_SCREENED"] += 1
            text = " ".join(row.get(name, "") for name in ("title", "body_text", "disaster_type_names", "theme_names"))
            links = json.loads(row.get("disaster_ids", "[]")); targets = json.loads(row.get("target_country_iso3_list", "[]"))
            candidate = _candidate(row, source="RELIEFWEB", record_type="REPORT", role="DOCUMENT_EVIDENCE", text=text,
                structured=[f"NATIVE_DISASTER_LINK:{item}" for item in links], geo_basis=row.get("target_geo_match_basis", "COUNTRY_LIST"),
                geo_strength="STRONG" if targets else "WEAK", native_link=bool(links))
            if not any(candidate[name] == "true" for name in ("candidate_flash_demand", "candidate_regional_emergency", "candidate_transport_network")): continue
            candidate.update({"country_iso3": targets[0] if targets else row.get("primary_country_iso3", ""),
                "native_disaster_link_present": _bool(bool(links)), "linked_disaster_ids": _j(map(str, links))})
            rw.writerow(candidate); iw.writerow(candidate); observe(candidate, hw); counts["RELIEFWEB_REPORT_CANDIDATES"] += 1

        anchor_specs = [
            ("reliefweb_disasters_standardized.tsv.gz", "RELIEFWEB", "DISASTER"), ("usgs_standardized.tsv.gz", "USGS", "EARTHQUAKE"),
            ("gdacs_standardized.tsv.gz", "GDACS", "DISASTER"), ("desinventar_standardized.tsv.gz", "DESINVENTAR", "DISASTER_LOSS"),
            ("copernicus_ems_standardized.tsv.gz", "COPERNICUS_EMS", "ACTIVATION"),
        ]
        for filename, source, record_type in anchor_specs:
            for row in read_tsv(processed / filename):
                text = " ".join(row.get(name, "") for name in ("event_name", "event_description", "event_type_raw", "event_subtype_raw", "title_raw", "description_raw", "location_raw", "primary_disaster_type_name", "disaster_type_names", "native_transporte"))
                force_regional = source in {"USGS", "GDACS", "DESINVENTAR"}
                force_transport = str(row.get("native_transporte", "")).strip().lower() not in {"", "0", "false", "none", "null"}
                structured = [f"SOURCE_NATIVE_TYPE:{row.get('event_type_raw') or row.get('primary_disaster_type_name') or record_type}"]
                if force_transport:
                    structured.append("SOURCE_NATIVE_TRANSPORT_IMPACT")
                country = row.get("country_iso3", row.get("primary_country_iso3", ""))
                candidate = _candidate(row, source=source, record_type=record_type, role="EVENT_ANCHOR", text=text,
                    structured=structured, geo_basis="SOURCE_NATIVE_COUNTRY_LIST", geo_strength="STRONG" if (country in TARGET_ISO3 or row.get("target_country_match") == "true") else "WEAK",
                    force_regional=force_regional, force_transport=force_transport)
                aw.writerow(candidate); iw.writerow(candidate); observe(candidate, hw); counts[f"ANCHOR_{source}"] += 1

    sample_rows: list[dict[str, Any]] = [] ; used: set[tuple[str, str, str]] = set()
    for (fam, pri), entries in sorted(sample_bins.items()):
        for _, row in entries:
            key = (row["source"], row["source_record_type"], row["source_record_id"])
            if key in used: continue
            used.add(key); sample_rows.append({"risk_family": fam, "record_id": row["source_record_id"], "title_text_excerpt": row["title_text_excerpt"],
                "rule_hits": "|".join((row["flash_rule_hits"], row["regional_rule_hits"], row["transport_asset_hits"], row["transport_disruption_hits"], row["structured_metadata_hits"])),
                "geo_evidence": f"{row['geo_match_basis']}:{row['geo_evidence_strength']}", "candidate_flash_demand": row["candidate_flash_demand"],
                "candidate_regional_emergency": row["candidate_regional_emergency"], "candidate_transport_network": row["candidate_transport_network"],
                "priority": pri, "source": row["source"], "source_url": row["source_url"]})
    _write_csv(audit / "stage5b2_candidate_audit_sample.csv", sample_rows)
    rule_rows = [{"signal_family": signal, "rule_hit": hit, "record_count": count} for (signal, hit), count in sorted(rules.items(), key=lambda item: (-item[1], item[0]))]
    geo_rows = [{"geo_match_basis": basis, "geo_evidence_strength": strength, "candidate_count": count} for (basis, strength), count in sorted(geo.items())]
    source_rows = [{"source": source, "source_record_type": typ, "candidate_count": count,
        "flash_demand_candidates": source_family.get((source, typ, "FLASH_DEMAND_SURGE"), 0),
        "regional_emergency_candidates": source_family.get((source, typ, "REGIONAL_EMERGENCY_DISRUPTION"), 0),
        "transport_network_candidates": source_family.get((source, typ, "TRANSPORT_NETWORK_DISRUPTION"), 0)}
        for (source, typ), count in sorted(sources.items())]
    summary_rows = ([{"metric": key, "value": value} for key, value in sorted(counts.items())] +
                    [{"metric": f"FAMILY_{key}", "value": value} for key, value in sorted(family.items())] +
                    [{"metric": f"PRIORITY_{key}", "value": value} for key, value in sorted(priority.items())])
    _write_csv(audit / "stage5b2_candidate_summary.csv", summary_rows, ["metric", "value"])
    _write_csv(audit / "stage5b2_rule_frequency.csv", rule_rows)
    _write_csv(audit / "stage5b2_geo_evidence_summary.csv", geo_rows)
    _write_csv(audit / "stage5b2_source_breakdown.csv", source_rows)
    after = {path: file_sha256(Path(path)) for path in before}
    if before != after: raise RuntimeError("A standardized input changed during screening")
    summary = {"counts": dict(counts), "family_counts": dict(family), "priority_counts": dict(priority), "country_counts": dict(countries),
        "overlap_counts": dict(overlaps), "geo_counts": {f"{k[0]}|{k[1]}": v for k, v in geo.items()}, "top_rule_hits": rule_rows[:50],
        "source_breakdown": source_rows, "audit_sample_size": len(sample_rows), "input_sha_unchanged": True,
        "input_sha256": before, "outputs": {k: str(v.resolve()) for k, v in output_paths.items()},
        "output_sha256": {k: file_sha256(v) for k, v in output_paths.items()}}
    _write_json(audit / "stage5b2_candidate_summary.json", summary)
    _write_report(audit / "stage5b2_candidate_report.md", summary)
    return summary


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = ["# Stage 5B-2 high-recall candidate screening", "", "This deterministic stage identifies source records worth later event identification. It does not create canonical events, infer cross-source links, assign severity, calibrate probabilities, map optimization parameters, or call GenAI.", "",
        "## Frozen rules and score", "", "Flash demand requires a direct demand/purchasing surge phrase, or a shortage phrase plus explicit demand-pressure context. Promotion, holiday, and shortage-only text do not qualify. Transport requires both an asset/context hit and a disruption-state hit unless source-native transport metadata is explicit. Structured source metadata outranks text; CAMEO codes 023/070-075 and 144 are auxiliary only and never create a family candidate alone.", "",
        "Score weights: structured event anchor +6; each candidate family +4; geo STRONG/MEDIUM/WEAK +3/+2/+1; each structured metadata signal +2 capped at 3; CAMEO support +1 capped at 2; ReliefWeb native link +2. The score is a ranking device, not probability, severity, or calibrated confidence.", "",
        "Priority A denotes structured anchors, multiple rule families, target ActionGeo plus a strong family, or native-linked ReliefWeb evidence. Priority B denotes one strong family with adequate target geography. Priority C denotes weak or supporting context. Priority is not severity.", "",
        "## Counts", ""]
    lines.extend(f"- {key}: {value:,}" for key, value in sorted(summary["counts"].items()))
    lines += ["", "## Family counts", ""] + [f"- {key}: {value:,}" for key, value in sorted(summary["family_counts"].items())]
    lines += ["", "## Priority counts", ""] + [f"- {key}: {value:,}" for key, value in sorted(summary["priority_counts"].items())]
    lines += ["", "## Source breakdown", "", "| Source | Record type | Candidates/anchors | Flash | Regional | Transport |", "|---|---|---:|---:|---:|---:|"]
    lines += [f"| {row['source']} | {row['source_record_type']} | {row['candidate_count']:,} | {row['flash_demand_candidates']:,} | {row['regional_emergency_candidates']:,} | {row['transport_network_candidates']:,} |" for row in summary["source_breakdown"]]
    lines += ["", "## Family overlap", ""] + [f"- {key}: {value:,}" for key, value in sorted(summary["overlap_counts"].items())]
    lines += ["", "## Country distribution", ""] + [f"- {key}: {value:,}" for key, value in sorted(summary["country_counts"].items())]
    lines += ["", "## Geography evidence", ""] + [f"- {key}: {value:,}" for key, value in sorted(summary["geo_counts"].items())]
    lines += ["", "## Most frequent rule hits", ""] + [f"- {row['signal_family']} / {row['rule_hit']}: {row['record_count']:,}" for row in summary["top_rule_hits"][:25]]
    lines += ["", f"Deterministic human-audit sample size: {summary['audit_sample_size']:,} records.", ""]
    lines += ["", "## Known false-positive risks", "", "Disaster vocabulary in retrospective policy reports may describe background context rather than a discrete event. URL tokens can be less precise than article text. Actor geography is contextual and is therefore never STRONG. Structured anchors can include preparedness activations or low-impact events. Shortage language can describe supply failure rather than demand surge, so shortage-only matches are excluded.", "", "Input SHA-256 values were unchanged. Reports remain DOCUMENT_EVIDENCE; structured sources remain EVENT_ANCHOR; no cross-source fusion was performed.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
