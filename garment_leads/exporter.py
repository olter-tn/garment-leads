"""CSV and JSON export utilities."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from garment_leads import db

ExportFormat = Literal["csv", "json"]

EXPORT_COLUMNS = [
    "id",
    "phone_raw",
    "phone_normalized",
    "name",
    "what",
    "why",
    "intent",
    "intent_confidence",
    "intent_matched_keywords",
    "intent_raw_excerpt",
    "first_seen",
    "last_seen",
    "post_count",
    "links",
    "source_group_id",
    "raw_text",
]


def export_leads(
    db_path: str | Path,
    export_dir: str | Path,
    *,
    output_format: ExportFormat,
    intent: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> Path:
    """Export leads to CSV or JSON and return the output path."""

    rows = db.fetch_all_leads(db_path, intent=intent, since=since, limit=limit)
    out_dir = Path(export_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"_{intent}" if intent else ""
    path = out_dir / f"garment_leads{suffix}_{timestamp}.{output_format}"
    if output_format == "json":
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    if output_format == "csv":
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return path
    raise ValueError(f"Unsupported export format: {output_format}")
