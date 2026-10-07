from __future__ import annotations

import csv
csv.field_size_limit(100 * 1024 * 1024)

import gzip
import hashlib
import io
import json
import re
import signal
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


# ============================================================
# CONFIG
# ============================================================

REPO_ROOT = Path(__file__).resolve().parents[2]

TARGET_FILE = (
    REPO_ROOT
    / "data"
    / "local"
    / "processed_risk_corpus"
    / "gkg_targets"
    / "flash_gkg_target_events.tsv.gz"
)

RAW_DIR = (
    REPO_ROOT
    / "data"
    / "local"
    / "raw_risk_corpus"
    / "gdelt"
    / "gkg_flash_full_day"
)

OUTPUT_DIR = (
    REPO_ROOT
    / "data"
    / "local"
    / "processed_risk_corpus"
    / "gkg_enrichment"
    / "flash_full_day"
)

GDELT_BASE = "https://data.gdeltproject.org/gdeltv2"

# False: unmatched ZIP files are deleted after scanning to save disk.
# True: keep all downloaded GKG ZIP files.
KEEP_ALL_ZIPS = False

DOWNLOAD_TIMEOUT = 90
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 3

# GKG 2.1: 0=GKGRECORDID, 1=V2DATE, 2=SourceCollectionIdentifier,
# 3=SourceCommonName, 4=DocumentIdentifier (the document URL).
GKG_DOCUMENT_URL_INDEX = 4

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
}

CHECKPOINT_PATH = OUTPUT_DIR / "checkpoint.json"
MANIFEST_PATH = OUTPUT_DIR / "full_day_download_manifest.csv"
MATCHES_PATH = OUTPUT_DIR / "full_day_url_matches.tsv"
UNMATCHED_PATH = OUTPUT_DIR / "unmatched_targets.tsv"
SUMMARY_PATH = OUTPUT_DIR / "full_day_summary.json"


# ============================================================
# GLOBAL STATE
# ============================================================

STOP_REQUESTED = False


def _handle_signal(signum, frame):
    global STOP_REQUESTED
    STOP_REQUESTED = True
    print("\n[INFO] Stop requested. Finishing current safe step and saving checkpoint...")


signal.signal(signal.SIGINT, _handle_signal)
if hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, _handle_signal)


# ============================================================
# HELPERS
# ============================================================

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_url(url: str) -> str:
    if not url:
        return ""

    url = url.strip()
    if not url:
        return ""

    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower()

        if host.startswith("www."):
            host = host[4:]

        port = p.port
        if port and port not in (80, 443):
            netloc = f"{host}:{port}"
        else:
            netloc = host

        path = re.sub(r"/+", "/", p.path or "/")
        if path != "/" and path.endswith("/"):
            path = path[:-1]

        query_pairs = [
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS
        ]
        query_pairs.sort()
        query = urlencode(query_pairs, doseq=True)

        return urlunsplit(("https", netloc, path, query, ""))
    except Exception:
        return url.strip().lower()


def download(url: str, dest: Path) -> tuple[str, str]:
    if dest.exists() and dest.stat().st_size > 0:
        return "EXISTS", ""

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    if tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass

    headers = {
        "User-Agent": (
            "Mozilla/5.0 AcademicResearch "
            "FlashDemand-GKG-Targeted-Enrichment/1.1"
        )
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = Request(url, headers=headers)

            with urlopen(req, timeout=DOWNLOAD_TIMEOUT) as response:
                with tmp.open("wb") as f:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)

            tmp.replace(dest)
            return "DOWNLOADED", ""

        except HTTPError as e:
            if e.code == 404:
                if tmp.exists():
                    tmp.unlink()
                return "MISSING", "HTTP 404"

            if attempt == MAX_RETRIES:
                if tmp.exists():
                    tmp.unlink()
                return "ERROR", f"HTTP {e.code}"

        except (URLError, TimeoutError, ConnectionError) as e:
            if attempt == MAX_RETRIES:
                if tmp.exists():
                    tmp.unlink()
                return "ERROR", repr(e)

        time.sleep(RETRY_SLEEP_SECONDS * attempt)

    return "ERROR", "unknown"


def read_targets() -> list[dict]:
    if not TARGET_FILE.exists():
        raise FileNotFoundError(f"Target file not found:\n{TARGET_FILE}")

    with gzip.open(TARGET_FILE, "rt", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    if not rows:
        raise RuntimeError("Target file is empty.")

    required = {"GlobalEventID", "event_date", "SOURCEURL"}
    missing = required - set(rows[0].keys())

    if missing:
        raise RuntimeError(
            f"Target file missing columns: {sorted(missing)}\n"
            f"Available: {rows[0].keys()}"
        )

    return rows


def make_slots(date_str: str) -> list[datetime]:
    date_obj = datetime.strptime(date_str[:10], "%Y-%m-%d")
    return [date_obj + timedelta(minutes=15 * i) for i in range(96)]


def scan_gkg_zip(
    zip_path: Path,
    active_targets: dict[str, dict],
) -> list[dict]:
    matches = []

    exact_map = {}
    normalized_map = {}

    for event_id, target in active_targets.items():
        source_url = (target.get("SOURCEURL") or "").strip()

        if source_url:
            exact_map.setdefault(source_url, []).append(event_id)

            normalized = normalize_url(source_url)
            if normalized:
                normalized_map.setdefault(normalized, []).append(event_id)

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = [name for name in zf.namelist() if not name.endswith("/")]

            if not members:
                return []

            for member in members:
                with zf.open(member, "r") as raw:
                    text = io.TextIOWrapper(
                        raw,
                        encoding="utf-8",
                        errors="replace",
                        newline="",
                    )

                    reader = csv.reader(text, delimiter="\t")

                    for row_num, row in enumerate(reader, start=1):
                        if len(row) <= GKG_DOCUMENT_URL_INDEX:
                            continue

                        gkg_url = row[GKG_DOCUMENT_URL_INDEX].strip()

                        if not gkg_url:
                            continue

                        matched_event_ids = []
                        match_type = None

                        if gkg_url in exact_map:
                            matched_event_ids = exact_map[gkg_url]
                            match_type = "EXACT_URL"
                        else:
                            nurl = normalize_url(gkg_url)

                            if nurl and nurl in normalized_map:
                                matched_event_ids = normalized_map[nurl]
                                match_type = "NORMALIZED_URL"

                        if not matched_event_ids:
                            continue

                        for event_id in matched_event_ids:
                            matches.append(
                                {
                                    "GlobalEventID": event_id,
                                    "match_type": match_type,
                                    "target_SOURCEURL": active_targets[
                                        event_id
                                    ].get("SOURCEURL", ""),
                                    "gkg_SOURCEURL": gkg_url,
                                    "gkg_row_number": row_num,
                                    "gkg_raw_row": "\t".join(row),
                                }
                            )

    except zipfile.BadZipFile:
        print(f"[WARN] Bad ZIP: {zip_path}")

    return matches


def load_checkpoint():
    if not CHECKPOINT_PATH.exists():
        return None

    try:
        return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] Could not load checkpoint: {e}")
        return None


def save_checkpoint(
    unresolved: set[str],
    completed_timestamps: set[str],
    manifest: list[dict],
    all_matches: list[dict],
):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "unresolved": sorted(unresolved),
        "completed_timestamps": sorted(completed_timestamps),
        "manifest": manifest,
        "all_matches": all_matches,
    }

    tmp = CHECKPOINT_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(CHECKPOINT_PATH)


def write_outputs(
    targets: list[dict],
    target_by_id: dict[str, dict],
    unresolved: set[str],
    manifest: list[dict],
    all_matches: list[dict],
    dates: list[str],
):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest_fields = [
        "timestamp",
        "date",
        "remote_url",
        "local_file",
        "download_status",
        "error",
        "file_size",
        "sha256",
        "matched_target_count",
    ]

    with MANIFEST_PATH.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        writer = csv.DictWriter(f, fieldnames=manifest_fields)
        writer.writeheader()
        writer.writerows(manifest)

    match_fields = [
        "GlobalEventID",
        "match_type",
        "gkg_timestamp",
        "gkg_file",
        "gkg_sha256",
        "target_SOURCEURL",
        "gkg_SOURCEURL",
        "gkg_row_number",
        "gkg_raw_row",
    ]

    with MATCHES_PATH.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:
        writer = csv.DictWriter(
            f,
            delimiter="\t",
            fieldnames=match_fields,
        )
        writer.writeheader()
        for row in all_matches:
            writer.writerow(row)

    with UNMATCHED_PATH.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:
        output_fields = list(targets[0].keys())
        writer = csv.DictWriter(
            f,
            delimiter="\t",
            fieldnames=output_fields,
            extrasaction="ignore",
        )
        writer.writeheader()

        for event_id in sorted(unresolved):
            writer.writerow(target_by_id[event_id])

    summary = {
        "target_events": len(target_by_id),
        "target_dates": len(dates),
        "download_slots_attempted": len(manifest),
        "downloaded_or_existing": sum(
            1
            for r in manifest
            if r["download_status"] in {"DOWNLOADED", "EXISTS"}
        ),
        "missing_remote_files": sum(
            1
            for r in manifest
            if r["download_status"] == "MISSING"
        ),
        "download_errors": sum(
            1
            for r in manifest
            if r["download_status"] == "ERROR"
        ),
        "matched_events": len(
            {r["GlobalEventID"] for r in all_matches}
        ),
        "exact_url_match_rows": sum(
            1
            for r in all_matches
            if r["match_type"] == "EXACT_URL"
        ),
        "normalized_url_match_rows": sum(
            1
            for r in all_matches
            if r["match_type"] == "NORMALIZED_URL"
        ),
        "unmatched_events": len(unresolved),
        "unmatched_GlobalEventIDs": sorted(unresolved),
        "keep_all_zips": KEEP_ALL_ZIPS,
        "checkpoint_file": str(CHECKPOINT_PATH),
    }

    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return summary


# ============================================================
# MAIN
# ============================================================

def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    targets = read_targets()

    print("=" * 70)
    print("Stage 5B-2G Full-Day Targeted GKG Search (resume-capable)")
    print("=" * 70)
    print(f"Target file : {TARGET_FILE}")
    print(f"Targets     : {len(targets)}")

    target_by_id = {}

    for row in targets:
        event_id = str(row["GlobalEventID"]).strip()
        if event_id:
            target_by_id[event_id] = row

    dates = sorted({
        row["event_date"][:10]
        for row in target_by_id.values()
        if row.get("event_date")
    })

    print(f"Unique events: {len(target_by_id)}")
    print(f"Target dates: {len(dates)}")
    print("Dates:", ", ".join(dates))
    print()

    checkpoint = load_checkpoint()

    if checkpoint:
        unresolved = set(checkpoint.get("unresolved", []))
        completed_timestamps = set(checkpoint.get("completed_timestamps", []))
        manifest = checkpoint.get("manifest", [])
        all_matches = checkpoint.get("all_matches", [])

        # Safety: if target IDs changed, reset unresolved appropriately
        current_ids = set(target_by_id.keys())
        unresolved &= current_ids

        matched_ids = {
            row["GlobalEventID"]
            for row in all_matches
            if row.get("GlobalEventID") in current_ids
        }

        unresolved |= (current_ids - matched_ids - unresolved)

        print(
            f"[RESUME] Loaded checkpoint: "
            f"{len(completed_timestamps)} slots completed, "
            f"{len(unresolved)} targets unresolved."
        )
    else:
        unresolved = set(target_by_id.keys())
        completed_timestamps = set()
        manifest = []
        all_matches = []
        print("[START] No checkpoint found. Starting fresh.")

    for date_str in dates:

        events_for_date = {
            event_id: target_by_id[event_id]
            for event_id in unresolved
            if target_by_id[event_id]["event_date"][:10] == date_str
        }

        if not events_for_date:
            continue

        print()
        print("=" * 70)
        print(
            f"[DATE] {date_str} | "
            f"unresolved targets = {len(events_for_date)}"
        )
        print("=" * 70)

        slots = make_slots(date_str)

        for slot_index, dt in enumerate(slots, start=1):

            if STOP_REQUESTED:
                save_checkpoint(
                    unresolved,
                    completed_timestamps,
                    manifest,
                    all_matches,
                )
                summary = write_outputs(
                    targets,
                    target_by_id,
                    unresolved,
                    manifest,
                    all_matches,
                    dates,
                )
                print(
                    f"[STOPPED SAFELY] Checkpoint saved. "
                    f"Unmatched events: {summary['unmatched_events']}"
                )
                return

            events_for_date = {
                event_id: target_by_id[event_id]
                for event_id in unresolved
                if target_by_id[event_id]["event_date"][:10] == date_str
            }

            if not events_for_date:
                print(f"[EARLY STOP] All targets for {date_str} matched.")
                break

            timestamp = dt.strftime("%Y%m%d%H%M%S")

            if timestamp in completed_timestamps:
                print(
                    f"[{slot_index:02d}/96] {timestamp} | "
                    f"SKIP completed"
                )
                continue

            filename = f"{timestamp}.gkg.csv.zip"
            url = f"{GDELT_BASE}/{filename}"
            dest = RAW_DIR / filename

            print(
                f"[{slot_index:02d}/96] "
                f"{timestamp} | active={len(events_for_date)}"
            )

            status, message = download(url, dest)

            manifest_row = {
                "timestamp": timestamp,
                "date": date_str,
                "remote_url": url,
                "local_file": str(dest),
                "download_status": status,
                "error": message,
                "file_size": "",
                "sha256": "",
                "matched_target_count": 0,
            }

            if status in {"DOWNLOADED", "EXISTS"} and dest.exists():

                manifest_row["file_size"] = dest.stat().st_size
                manifest_row["sha256"] = sha256_file(dest)

                try:
                    matches = scan_gkg_zip(
                        dest,
                        events_for_date,
                    )
                except csv.Error as e:
                    # Defensive: with enlarged field limit this should be rare.
                    print(f"[WARN] CSV parse error in {filename}: {e}")
                    matches = []
                    manifest_row["error"] = f"CSV_PARSE_ERROR: {e}"

                if matches:
                    found_ids = {m["GlobalEventID"] for m in matches}

                    manifest_row["matched_target_count"] = len(found_ids)

                    for m in matches:
                        m["gkg_timestamp"] = timestamp
                        m["gkg_file"] = filename
                        m["gkg_sha256"] = manifest_row["sha256"]

                    all_matches.extend(matches)

                    print(
                        f"    >>> MATCHED EVENTS: "
                        f"{sorted(found_ids)}"
                    )

                    unresolved -= found_ids

                if (
                    not KEEP_ALL_ZIPS
                    and manifest_row["matched_target_count"] == 0
                ):
                    try:
                        dest.unlink()
                    except OSError:
                        pass

            manifest.append(manifest_row)
            completed_timestamps.add(timestamp)

            save_checkpoint(
                unresolved,
                completed_timestamps,
                manifest,
                all_matches,
            )

    summary = write_outputs(
        targets,
        target_by_id,
        unresolved,
        manifest,
        all_matches,
        dates,
    )

    save_checkpoint(
        unresolved,
        completed_timestamps,
        manifest,
        all_matches,
    )

    print()
    print("=" * 70)
    print("FINISHED")
    print("=" * 70)
    print(f"Target events     : {summary['target_events']}")
    print(f"Slots attempted   : {summary['download_slots_attempted']}")
    print(f"Matched events    : {summary['matched_events']}")
    print(f"Unmatched events  : {summary['unmatched_events']}")

    print()
    print("Outputs:")
    print(f"  {MANIFEST_PATH}")
    print(f"  {MATCHES_PATH}")
    print(f"  {UNMATCHED_PATH}")
    print(f"  {SUMMARY_PATH}")
    print(f"  {CHECKPOINT_PATH}")

    if unresolved:
        print()
        print(
            "NOTE: unmatched means no URL match was found "
            "on the TARGET DATE full-day GKG slots."
        )
        print(
            "Do NOT interpret this as proof that "
            "the event is absent from GKG."
        )


if __name__ == "__main__":
    main()
