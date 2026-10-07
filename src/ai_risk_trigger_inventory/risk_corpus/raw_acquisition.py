"""Small source-preserving utilities for Stage-5A raw acquisition."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


REQUESTED_START = "2015-01-01T00:00:00Z"
REQUESTED_END = "2026-08-31T23:59:59Z"
TARGET_COUNTRIES = ("Ecuador", "Colombia", "Peru")
TARGET_BBOX = {
    "minlatitude": -19,
    "maxlatitude": 13,
    "minlongitude": -83,
    "maxlongitude": -66,
}
USER_AGENT = "ai-risk-trigger-inventory-stage5a/1.0 (public research corpus)"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_bytes(
    *,
    source: str,
    endpoint: str,
    parameters: dict[str, Any] | None,
    destination: Path,
    timeout: int = 120,
) -> tuple[dict[str, Any], bytes | None]:
    query = urllib.parse.urlencode(parameters or {}, doseq=True)
    url = endpoint + (("&" if "?" in endpoint else "?") + query if query else "")
    timestamp = utc_timestamp()
    row: dict[str, Any] = {
        "source": source,
        "filename": str(destination.name),
        "source_url": endpoint,
        "query_parameters": json.dumps(parameters or {}, sort_keys=True),
        "requested_url": url,
        "download_timestamp": timestamp,
        "http_status": None,
        "status_result": "DOWNLOAD_FAILED",
        "size_bytes": 0,
        "sha256": "",
        "record_count": None,
        "error": "",
    }
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
            row["http_status"] = response.status
            row["content_type"] = response.headers.get("Content-Type", "")
            row["resolved_url"] = response.geturl()
    except urllib.error.HTTPError as error:
        body = error.read(2048)
        row["http_status"] = error.code
        row["error"] = body.decode("utf-8", errors="replace")
        return row, None
    except Exception as error:  # Network failures must be recorded, not hidden.
        row["error"] = f"{type(error).__name__}: {error}"
        return row, None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    row["status_result"] = "DOWNLOADED"
    row["size_bytes"] = len(payload)
    row["sha256"] = sha256(payload).hexdigest()
    return row, payload


def json_record_count(value: Any) -> int | None:
    if isinstance(value, dict):
        for key in ("features", "results", "data", "items"):
            if isinstance(value.get(key), list):
                return len(value[key])
    if isinstance(value, list):
        return len(value)
    return None


def schema_snapshot(value: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {"top_level_type": type(value).__name__}
    if isinstance(value, dict):
        snapshot["top_level_keys"] = sorted(value)
        for key in ("features", "results", "data", "items"):
            rows = value.get(key)
            if isinstance(rows, list):
                snapshot["record_container"] = key
                snapshot["record_count_in_sample"] = len(rows)
                if rows and isinstance(rows[0], dict):
                    snapshot["record_keys"] = sorted(rows[0])
                    properties = rows[0].get("properties")
                    if isinstance(properties, dict):
                        snapshot["properties_keys"] = sorted(properties)
                break
    elif isinstance(value, list) and value and isinstance(value[0], dict):
        snapshot["record_keys"] = sorted(value[0])
    return snapshot


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def finalize_source(
    source_dir: Path,
    manifest: dict[str, Any],
    logs: list[dict[str, Any]],
    schema: dict[str, Any],
) -> None:
    write_json(source_dir / "manifest.json", manifest)
    write_csv(source_dir / "download_log.csv", logs)
    write_json(source_dir / "schema_snapshot.json", schema)
