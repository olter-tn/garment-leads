from __future__ import annotations

from pathlib import Path

import pytest

from garment_leads.config import Settings
from garment_leads.scraper import ScrapeFailure, parse_posts_from_html, scrape_group


def _settings(tmp_path: Path, sample_html: Path) -> Settings:
    return Settings(
        group_id="284751225383775",
        group_url="https://example.invalid/group",
        db_path=tmp_path / "test.sqlite3",
        export_dir=tmp_path / "exports",
        log_dir=tmp_path / "logs",
        raw_response_dir=tmp_path / "raw",
        sample_html_path=sample_html,
        request_timeout_seconds=0.1,
        rate_limit_seconds=0.0,
        flask_host="127.0.0.1",
        flask_port=5001,
        user_agents=("pytest-agent",),
        fb_cookies_path=tmp_path / "fb_cookies.json",
        fb_cookie_max_age_days=30,
        fb_min_delay_seconds=0.0,
        fb_max_delay_seconds=0.0,
        fb_max_requests_per_minute=999,
        fb_max_retries=0,
    )


def test_parse_posts_from_article_html() -> None:
    html = """
    <html><body>
      <article data-post-id="a1"><p>Atelier couture Tel 54 123 456</p><a href="/p/a1">link</a></article>
      <article data-post-id="a2"><p>مطلوب خياطات Tel 55 123 987</p></article>
    </body></html>
    """
    posts = parse_posts_from_html(html)
    assert len(posts) == 2
    assert posts[0].id == "a1"
    assert "Atelier" in posts[0].text
    assert posts[0].link == "https://mbasic.facebook.com/p/a1"


def test_parse_posts_limit() -> None:
    html = "".join(f"<article data-post-id='{i}'>Atelier couture Tel 54 123 45{i}</article>" for i in range(5))
    assert len(parse_posts_from_html(html, limit=2)) == 2


def test_cache_only_uses_sample(tmp_path: Path) -> None:
    sample = tmp_path / "sample.html"
    sample.write_text("<article data-post-id='x'>Atelier couture Tel 54 123 456</article>", encoding="utf-8")
    outcome = scrape_group(_settings(tmp_path, sample), cache_only=True)
    assert outcome.source_mode == "cached"
    assert outcome.status == "cache_only"
    assert len(outcome.posts) == 1


def test_no_cache_raises_on_live_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sample = tmp_path / "sample.html"
    sample.write_text("<article data-post-id='x'>Atelier couture Tel 54 123 456</article>", encoding="utf-8")

    def fake_fetch(*_args, **_kwargs):
        raise ScrapeFailure("network_error", "network down", error_class="ConnectionError")

    monkeypatch.setattr("garment_leads.scraper.fetch_live_html", fake_fetch)
    with pytest.raises(ScrapeFailure) as exc:
        scrape_group(_settings(tmp_path, sample), no_cache=True)
    assert exc.value.status == "network_error"


def test_live_failure_falls_back_loud_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sample = tmp_path / "sample.html"
    sample.write_text("<article data-post-id='x'>Atelier couture Tel 54 123 456</article>", encoding="utf-8")

    def fake_fetch(*_args, **_kwargs):
        raise ScrapeFailure("login_wall", "login required", error_class="LoginWall")

    monkeypatch.setattr("garment_leads.scraper.fetch_live_html", fake_fetch)
    outcome = scrape_group(_settings(tmp_path, sample))
    assert outcome.source_mode == "cached"
    assert outcome.status == "live_failed_cache_used"
    assert outcome.error_class == "LoginWall"


def test_empty_cache_raises_markup_changed(tmp_path: Path) -> None:
    sample = tmp_path / "empty.html"
    sample.write_text("<html><body></body></html>", encoding="utf-8")
    with pytest.raises(ScrapeFailure) as exc:
        scrape_group(_settings(tmp_path, sample), cache_only=True)
    assert exc.value.status == "empty_or_markup_changed"
