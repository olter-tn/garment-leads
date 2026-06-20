"""v3 additions: FB cookies, auth session, parser enrichment, migration."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import requests

from garment_leads import db
from garment_leads.auth import (
    FACEBOOK_DOMAIN,
    build_authenticated_session,
    mobile_group_url,
)
from garment_leads.cadence import CadenceConfig, Pacer
from garment_leads.fb_cookies import (
    CookieSnapshot,
    cookies_are_fresh,
    default_cookie_path,
    freshness_days,
    load_cookies,
    mask_c_user,
    save_cookies,
    validate,
)
from garment_leads.parser import (
    extract_author_from_html,
    extract_comments_count_from_html,
    extract_permalink_from_html,
    extract_reactions_count_from_html,
    extract_timestamp_unix_from_html,
    parse_post_text,
)

# ---------------- CookieSnapshot / validate / mask ----------------

class TestCookieSnapshot:
    def test_required_fields_present(self) -> None:
        snap = CookieSnapshot(c_user="123456789012345", xs="47:abc")
        d = snap.to_json_dict()
        assert d["c_user"] == "123456789012345"
        assert d["xs"] == "47:abc"
        assert "captured_at" in d

    def test_validate_accepts_well_formed(self) -> None:
        snap = CookieSnapshot(c_user="123456789012345", xs="47:abc")
        assert validate(snap) == []

    def test_validate_rejects_bad_c_user(self) -> None:
        snap = CookieSnapshot(c_user="not-a-number", xs="47:abc")
        problems = validate(snap)
        assert any("c_user" in p for p in problems)

    def test_validate_rejects_empty_xs(self) -> None:
        snap = CookieSnapshot(c_user="123456789012345", xs="")
        problems = validate(snap)
        assert any("xs" in p for p in problems)

    def test_mask_c_user(self) -> None:
        # 15-char ID: first 4 + 7 stars + last 4 = 15 chars total
        assert mask_c_user("100015190744255") == "1000*******4255"
        assert mask_c_user("12") == "**"
        assert mask_c_user("") == ""
        # 9-char ID: first 4 + 1 star + last 4
        assert mask_c_user("123456789") == "1234*6789"

    def test_freshness_zero_days(self) -> None:
        snap = CookieSnapshot(
            c_user="123456789012345",
            xs="47:abc",
            captured_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        )
        age = freshness_days(snap)
        assert age is not None
        assert 0.0 <= age < 0.1

    def test_freshness_30_days_old(self) -> None:
        old = (datetime.now(UTC) - timedelta(days=30)).replace(microsecond=0).isoformat()
        snap = CookieSnapshot(c_user="123456789012345", xs="47:abc", captured_at=old)
        age = freshness_days(snap)
        assert age is not None
        assert 29.5 < age < 30.5

    def test_freshness_unparseable(self) -> None:
        snap = CookieSnapshot(c_user="123456789012345", xs="47:abc", captured_at="not-a-date")
        assert freshness_days(snap) is None
        assert not cookies_are_fresh(snap)

    def test_freshness_under_max_age(self) -> None:
        recent = (datetime.now(UTC) - timedelta(days=5)).replace(microsecond=0).isoformat()
        snap = CookieSnapshot(c_user="123456789012345", xs="47:abc", captured_at=recent)
        assert cookies_are_fresh(snap, max_age_days=30)
        assert not cookies_are_fresh(snap, max_age_days=2)

    def test_round_trip_json(self) -> None:
        snap = CookieSnapshot(
            c_user="100015190744255",
            xs="47:abc",
            fr="abc",
            datr="def",
            sb="ghi",
            locale="fr_FR",
            captured_at="2026-06-19T21:30:00+00:00",
        )
        data = snap.to_json_dict()
        snap2 = CookieSnapshot.from_json_dict(data)
        assert snap2.c_user == snap.c_user
        assert snap2.xs == snap.xs
        assert snap2.locale == snap.locale
        assert snap2.captured_at == snap.captured_at


# ---------------- save / load round-trip ----------------

class TestCookieIO:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        snap = CookieSnapshot(
            c_user="123456789012345",
            xs="47:abc",
            fr="fr-val",
            captured_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        )
        path = save_cookies(snap, path=tmp_path / "fb_cookies.json")
        assert path.exists()
        # File permissions
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"
        loaded = load_cookies(path=path, max_age_days=30)
        assert loaded is not None
        assert loaded.c_user == snap.c_user
        assert loaded.xs == snap.xs
        assert loaded.fr == snap.fr

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        assert load_cookies(path=tmp_path / "nope.json") is None

    def test_load_invalid_json_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json")
        assert load_cookies(path=path) is None

    def test_load_stale_returns_none(self, tmp_path: Path) -> None:
        old = (datetime.now(UTC) - timedelta(days=31)).replace(microsecond=0).isoformat()
        snap = CookieSnapshot(c_user="123456789012345", xs="47:abc", captured_at=old)
        save_cookies(snap, path=tmp_path / "stale.json")
        assert load_cookies(path=tmp_path / "stale.json", max_age_days=30) is None

    def test_save_atomic_uses_replace(self, tmp_path: Path) -> None:
        snap1 = CookieSnapshot(c_user="111", xs="aaa")
        snap2 = CookieSnapshot(c_user="222", xs="bbb")
        path = tmp_path / "x.json"
        save_cookies(snap1, path=path)
        save_cookies(snap2, path=path)
        # No .tmp left behind
        leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".fb_cookies.") and p.name.endswith(".tmp")]
        assert leftover == []
        # Final contents are from snap2
        data = json.loads(path.read_text())
        assert data["c_user"] == "222"

    def test_default_path_xdg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg-test")
        path = default_cookie_path()
        assert path == Path("/tmp/xdg-test/garment-leads/fb_cookies.json")

    def test_default_path_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        path = default_cookie_path()
        assert path == Path.home() / ".config" / "garment-leads" / "fb_cookies.json"


# ---------------- Auth session ----------------

class TestAuthSession:
    def test_build_session_sets_cookies(self) -> None:
        snap = CookieSnapshot(c_user="123456789012345", xs="47:abc", fr="fr-val", datr="d-val", sb="s-val", locale="fr_FR")
        session = build_authenticated_session(snap, user_agent="TestAgent/1.0")
        # Cookies on the .facebook.com domain
        assert session.cookies.get("c_user", domain=FACEBOOK_DOMAIN) == "123456789012345"
        assert session.cookies.get("xs", domain=FACEBOOK_DOMAIN) == "47:abc"
        assert session.cookies.get("fr", domain=FACEBOOK_DOMAIN) == "fr-val"
        # Headers
        assert session.headers["User-Agent"] == "TestAgent/1.0"
        assert "Accept-Language" in session.headers
        # Locale-aware Accept-Language starts with fr-FR or fr
        assert "fr" in session.headers["Accept-Language"]

    def test_build_session_minimal_cookies(self) -> None:
        snap = CookieSnapshot(c_user="123456789012345", xs="47:abc")
        session = build_authenticated_session(snap, user_agent="TestAgent/1.0")
        # fr, datr, sb not set => not in cookies
        assert session.cookies.get("fr", domain=FACEBOOK_DOMAIN) is None

    def test_mobile_group_url(self) -> None:
        assert mobile_group_url("284751225383775") == "https://m.facebook.com/groups/284751225383775/"

    def test_session_is_requests_session(self) -> None:
        snap = CookieSnapshot(c_user="123456789012345", xs="47:abc")
        session = build_authenticated_session(snap, user_agent="TestAgent/1.0")
        assert isinstance(session, requests.Session)


# ---------------- Cadence (Pacer) ----------------

class TestCadence:
    def test_cadence_config_validation(self) -> None:
        with pytest.raises(ValueError):
            CadenceConfig(min_delay_seconds=-1.0)
        with pytest.raises(ValueError):
            CadenceConfig(min_delay_seconds=10.0, max_delay_seconds=5.0)
        with pytest.raises(ValueError):
            CadenceConfig(max_requests_per_minute=0)

    def test_pacer_jitter_within_range(self) -> None:
        cfg = CadenceConfig(min_delay_seconds=2.0, max_delay_seconds=8.0)
        pacer = Pacer(
            config=cfg,
            sleep_fn=lambda _x: None,
            rand_fn=lambda lo, hi: (lo + hi) / 2.0,
        )
        d = pacer.jitter_seconds()
        assert 2.0 <= d <= 8.0

    def test_pacer_does_not_sleep_under_cap(self) -> None:
        cfg = CadenceConfig(min_delay_seconds=0.0, max_delay_seconds=0.0, max_requests_per_minute=8)
        slept: list[float] = []
        pacer = Pacer(config=cfg, sleep_fn=slept.append, rand_fn=lambda _lo, _hi: 0.0, now_fn=lambda: 100.0)
        # First call: jitter=0, cap not hit → no sleep needed at all
        pacer.before_request()
        # The pacer should NOT sleep (jitter is 0 and cap not hit), but if it does
        # sleep (0.0), it should record a 0.0 entry. Either is acceptable; the key
        # property is that no real wait was introduced.
        for s in slept:
            assert s == 0.0

    def test_pacer_per_minute_cap(self) -> None:
        cfg = CadenceConfig(min_delay_seconds=0.0, max_delay_seconds=0.0, max_requests_per_minute=2)
        slept: list[float] = []
        now = [100.0]
        pacer = Pacer(
            config=cfg,
            sleep_fn=slept.append,
            rand_fn=lambda _lo, _hi: 0.0,
            now_fn=lambda: now[0],
        )
        pacer.before_request()  # 1
        pacer.before_request()  # 2 — hits cap
        # Third call: must wait until oldest slot expires (60s later)
        pacer.before_request()
        # The sleep should be > 0
        assert any(s > 0 for s in slept)
        # And the wait should be approximately 60 seconds minus a tiny epsilon
        assert max(slept) >= 59.5

    def test_pacer_backoff_sequence(self) -> None:
        cfg = CadenceConfig(max_retries=3)
        pacer = Pacer(config=cfg, sleep_fn=lambda _x: None, rand_fn=lambda lo, hi: (lo + hi) / 2)
        assert pacer.backoff_seconds(0) == 5
        assert pacer.backoff_seconds(1) == 15
        assert pacer.backoff_seconds(2) == 45
        assert pacer.backoff_seconds(3) == 0  # beyond max_retries

    def test_pacer_observed_avg(self) -> None:
        cfg = CadenceConfig(min_delay_seconds=1.0, max_delay_seconds=5.0)
        pacer = Pacer(
            config=cfg,
            sleep_fn=lambda _x: None,
            rand_fn=lambda lo, hi: 3.0,
        )
        for _ in range(4):
            pacer.jitter_seconds()
        assert pacer.observed_avg_delay() == 3.0


# ---------------- Parser enrichment (logged-in HTML) ----------------

SAMPLE_LOGGED_IN_POST_HTML = """
<article data-ft='{"fbfeedid":"12345"}'>
  <h3 class="be f3"><a href="/profile.php?id=100015190744255">Selim Nasri</a></h3>
  <div class="_5rgt">5 hours ago</div>
  <abbr title="2026-06-19T16:00:00+00:00">5h</abbr>
  <div class="userContent">Cherche couturière pour atelier à Sousse. 22 333 444</div>
  <a href="https://m.facebook.com/groups/284751225383775/permalink/1234567890/">Permalink</a>
  <span aria-label="5 reactions">5</span>
  <a href="#">2 comments</a>
</article>
"""


class TestParserEnrichment:
    def test_extract_author(self) -> None:
        assert extract_author_from_html(SAMPLE_LOGGED_IN_POST_HTML) == "Selim Nasri"

    def test_extract_permalink(self) -> None:
        url = extract_permalink_from_html(SAMPLE_LOGGED_IN_POST_HTML, group_id="284751225383775")
        assert url is not None
        assert "/284751225383775/permalink/1234567890/" in url

    def test_extract_timestamp_from_abbr(self) -> None:
        ts = extract_timestamp_unix_from_html(SAMPLE_LOGGED_IN_POST_HTML)
        assert ts is not None
        # 2026-06-19T16:00:00+00:00 → 1781884800 (verified empirically)
        assert abs(ts - 1781884800) < 60

    def test_extract_reactions_count(self) -> None:
        assert extract_reactions_count_from_html(SAMPLE_LOGGED_IN_POST_HTML) == 5

    def test_extract_comments_count(self) -> None:
        assert extract_comments_count_from_html(SAMPLE_LOGGED_IN_POST_HTML) == 2

    def test_extract_returns_none_on_empty(self) -> None:
        assert extract_author_from_html("") is None
        assert extract_permalink_from_html("", "123") is None
        assert extract_timestamp_unix_from_html("") is None
        assert extract_reactions_count_from_html("") is None
        assert extract_comments_count_from_html("") is None

    def test_parse_post_text_with_enrichment(self) -> None:
        leads = parse_post_text(
            "Cherche couturière pour atelier à Sousse. 22 333 444",
            source_group_id="284751225383775",
            author="Selim Nasri",
            timestamp_unix=1747756800,
            permalink="https://m.facebook.com/groups/284751225383775/permalink/1234567890/",
            reactions_count=5,
            comments_count=2,
        )
        assert len(leads) == 1
        lead = leads[0]
        assert lead.author == "Selim Nasri"
        assert lead.timestamp_unix == 1747756800
        assert lead.permalink is not None
        assert lead.reactions_count == 5
        assert lead.comments_count == 2
        assert lead.permalink in lead.links

    def test_parse_post_text_without_enrichment(self) -> None:
        # Backward compat: the v2 call signature still works
        leads = parse_post_text("Cherche couturière 22 333 444", source_group_id="284751225383775")
        assert len(leads) == 1
        assert leads[0].author is None
        assert leads[0].timestamp_unix is None
        assert leads[0].permalink is None


# ---------------- DB migration ----------------

class TestV3Migration:
    def test_init_db_adds_v3_columns(self, tmp_path: Path) -> None:
        # Create an empty DB
        db_path = tmp_path / "test.sqlite3"
        db.init_db(db_path)
        # Confirm v3 columns exist
        with db.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(leads)").fetchall()}
        for col in ("author", "timestamp_unix", "permalink", "reactions_count", "comments_count"):
            assert col in cols, f"missing {col}"
        with db.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(scrape_runs)").fetchall()}
        for col in ("posts_requested", "source_kind"):
            assert col in cols, f"missing {col}"

    def test_init_db_idempotent(self, tmp_path: Path) -> None:
        # Run init_db twice; should not raise
        db_path = tmp_path / "test.sqlite3"
        db.init_db(db_path)
        db.init_db(db_path)  # duplicate-column errors are caught

    def test_upsert_lead_with_v3_fields(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.sqlite3"
        db.init_db(db_path)
        leads = parse_post_text(
            "Cherche couturière 22 333 444",
            source_group_id="284751225383775",
            author="Selim Nasri",
            timestamp_unix=1747756800,
            permalink="https://m.facebook.com/groups/284751225383775/permalink/1234567890/",
            reactions_count=5,
            comments_count=2,
        )
        assert db.upsert_leads(db_path, leads) == (1, 0)
        with db.connect(db_path) as conn:
            row = conn.execute("SELECT * FROM leads").fetchone()
        assert row["author"] == "Selim Nasri"
        assert row["timestamp_unix"] == 1747756800
        assert row["reactions_count"] == 5
        assert row["comments_count"] == 2

    def test_record_scrape_run_with_v3_fields(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.sqlite3"
        db.record_scrape_run(
            db_path,
            started_at="2026-06-19T21:00:00+00:00",
            finished_at="2026-06-19T21:01:00+00:00",
            source_mode="live",
            status="live_success",
            posts_fetched=10,
            posts_parsed=10,
            leads_inserted=5,
            leads_skipped_duplicate=0,
            posts_requested=10,
            source_kind="authenticated",
        )
        runs = db.fetch_scrape_runs(db_path, limit=1)
        assert runs[0]["posts_requested"] == 10
        assert runs[0]["source_kind"] == "authenticated"