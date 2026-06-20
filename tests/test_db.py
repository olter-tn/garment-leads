from __future__ import annotations

import sqlite3
from pathlib import Path

from garment_leads import db
from garment_leads.parser import parse_post_text


def _lead(text: str):
    return parse_post_text(text, source_group_id="284751225383775")[0]


def test_init_db_creates_required_tables_and_indexes(tmp_path: Path) -> None:
    path = tmp_path / "leads.sqlite3"
    db.init_db(path)
    with sqlite3.connect(path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"leads", "scrape_runs"}.issubset(tables)
    assert "idx_leads_phone_normalized" in indexes
    assert "idx_leads_intent" in indexes
    assert "idx_leads_source_group_id" in indexes
    assert "idx_leads_first_seen" in indexes
    assert "idx_leads_last_seen" in indexes


def test_upsert_deduplicates_by_normalized_phone(tmp_path: Path) -> None:
    path = tmp_path / "leads.sqlite3"
    first = _lead("Atelier couture Tel 54 123 456")
    second = _lead("Atelier retouche Tel +216 54 123 456")
    assert db.upsert_lead(path, first) is True
    assert db.upsert_lead(path, second) is False
    rows = db.fetch_all_leads(path)
    assert len(rows) == 1
    assert rows[0]["phone_normalized"] == "+21654123456"
    assert rows[0]["post_count"] == 2


def test_record_scrape_run_and_summary(tmp_path: Path) -> None:
    path = tmp_path / "leads.sqlite3"
    run_id = db.record_scrape_run(
        path,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:02+00:00",
        source_mode="cached",
        status="cache_only",
        posts_fetched=30,
        posts_parsed=30,
        leads_inserted=20,
        leads_skipped_duplicate=3,
        raw_response_path="sample.html",
    )
    assert run_id == 1
    runs = db.fetch_scrape_runs(path)
    assert runs[0]["source_mode"] == "cached"
    assert runs[0]["status"] == "cache_only"
    summary = db.scrape_run_summary(path)
    assert summary["last_successful_scrape"]["status"] == "cache_only"


def test_stats_queries_return_grouped_counts(tmp_path: Path) -> None:
    path = tmp_path / "leads.sqlite3"
    leads = parse_post_text("نبيع قماش ساتان Tel 52 333 444 et 53 444 555", source_group_id="284751225383775")
    db.upsert_leads(path, leads)
    assert db.count_leads(path) == 2
    assert db.counts_by_intent(path)[0]["intent"] == "fabric_supplier"
    prefixes = {row["prefix"] for row in db.counts_by_phone_prefix(path)}
    assert {"52", "53"}.issubset(prefixes)
