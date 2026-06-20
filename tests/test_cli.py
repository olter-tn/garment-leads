from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from garment_leads.cli import main


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "GARMENT_LEADS_DB_PATH": str(tmp_path / "test.sqlite3"),
        "GARMENT_LEADS_EXPORT_DIR": str(tmp_path / "exports"),
        "GARMENT_LEADS_LOG_DIR": str(tmp_path / "logs"),
        "GARMENT_LEADS_RAW_RESPONSE_DIR": str(tmp_path / "raw"),
    }


def test_cli_init_db(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["init-db"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "Initialized DB" in result.output
    assert (tmp_path / "test.sqlite3").exists()


def test_cli_scrape_cache_only_prints_provenance(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["scrape", "--cache-only", "--limit", "5"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "SOURCE_MODE=cached STATUS=cache_only" in result.output
    assert "posts_fetched=5" in result.output


def test_cli_stats_after_cache_scrape(tmp_path: Path) -> None:
    runner = CliRunner()
    env = _env(tmp_path)
    runner.invoke(main, ["scrape", "--cache-only", "--limit", "5"], env=env)
    result = runner.invoke(main, ["stats"], env=env)
    assert result.exit_code == 0
    assert "total_leads=" in result.output
    assert "scrape_provenance=" in result.output


def test_cli_export_filters_json(tmp_path: Path) -> None:
    runner = CliRunner()
    env = _env(tmp_path)
    runner.invoke(main, ["scrape", "--cache-only", "--limit", "8"], env=env)
    result = runner.invoke(main, ["export", "--format", "json", "--intent", "atelier", "--limit", "2"], env=env)
    assert result.exit_code == 0
    exported = list((tmp_path / "exports").glob("*.json"))
    assert exported
    rows = json.loads(exported[0].read_text(encoding="utf-8"))
    assert len(rows) <= 2
    assert all(row["intent"] == "atelier" for row in rows)


def test_cli_validate_fixtures(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["validate-fixtures"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "fixtures_checked=" in result.output
    assert "failures=0" in result.output


def test_cli_mutually_exclusive_cache_flags(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["scrape", "--cache-only", "--no-cache"], env=_env(tmp_path))
    assert result.exit_code == 1
    assert "mutually exclusive" in result.output
