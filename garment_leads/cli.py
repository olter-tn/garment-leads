"""Click command-line interface for garment-leads."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import requests

from garment_leads import __version__, db
from garment_leads.cadence import CadenceConfig, Pacer
from garment_leads.config import Settings, ensure_runtime_dirs, get_settings, mask_phone, setup_logging
from garment_leads.exporter import export_leads
from garment_leads.fb_cookies import (
    CookieSnapshot,
    capture_from_chromium,
    default_cookie_path,
    freshness_days,
    load_cookies,
    mask_c_user,
    save_cookies,
    validate,
)
from garment_leads.parser import VALID_INTENTS, parse_fixture_payload, parse_post_text
from garment_leads.scraper import ScrapeFailure, scrape_group

OK = 0
USER_ERROR = 1
SYSTEM_ERROR = 2


def _settings() -> Settings:
    settings = get_settings()
    ensure_runtime_dirs(settings)
    setup_logging(settings)
    return settings


def _loud_run_line(source_mode: str, status: str) -> str:
    return f"!!! SCRAPE RUN SOURCE_MODE={source_mode} STATUS={status} !!!"


def _record_failure(
    settings: Settings,
    started_at: str,
    exc: ScrapeFailure,
    *,
    posts_requested: int | None = None,
) -> int:
    source_kind = "authenticated" if load_cookies(settings.fb_cookies_path) is not None else "anonymous"
    return db.record_scrape_run(
        settings.db_path,
        started_at=started_at,
        finished_at=db.utc_now_iso(),
        source_mode="live",
        status=exc.status,
        posts_fetched=0,
        posts_parsed=0,
        leads_inserted=0,
        leads_skipped_duplicate=0,
        error_class=exc.error_class,
        error_detail=str(exc),
        raw_response_path=exc.raw_response_path,
        posts_requested=posts_requested,
        source_kind=source_kind,
    )


def _run_scrape(
    settings: Settings,
    *,
    no_cache: bool,
    cache_only: bool,
    limit: int | None,
    limit_posts: int | None = None,
    pacer: Pacer | None = None,
) -> tuple[int, dict[str, Any]]:
    started_at = db.utc_now_iso()
    try:
        outcome = scrape_group(
            settings,
            no_cache=no_cache,
            cache_only=cache_only,
            limit=limit,
            pacer=pacer,
        )
    except ScrapeFailure as exc:
        run_id = _record_failure(settings, started_at, exc, posts_requested=limit_posts)
        return SYSTEM_ERROR, {
            "run_id": run_id,
            "source_mode": "live",
            "status": exc.status,
            "posts_fetched": 0,
            "posts_parsed": 0,
            "leads_inserted": 0,
            "leads_skipped_duplicate": 0,
            "error_class": exc.error_class,
            "error_detail": str(exc),
        }

    all_leads = []
    posts_parsed = 0
    try:
        for post in outcome.posts:
            leads = parse_post_text(
                post.text,
                source_group_id=settings.group_id,
                link=post.link,
            )
            posts_parsed += 1
            all_leads.extend(leads)
            if limit_posts is not None and posts_parsed >= limit_posts:
                break
        inserted, duplicates = db.upsert_leads(settings.db_path, all_leads)
        source_kind = "authenticated" if load_cookies(settings.fb_cookies_path) is not None else "anonymous"
        run_id = db.record_scrape_run(
            settings.db_path,
            started_at=started_at,
            finished_at=db.utc_now_iso(),
            source_mode=outcome.source_mode,
            status=outcome.status,
            posts_fetched=len(outcome.posts),
            posts_parsed=posts_parsed,
            leads_inserted=inserted,
            leads_skipped_duplicate=duplicates,
            error_class=outcome.error_class,
            error_detail=outcome.error_detail,
            raw_response_path=outcome.raw_response_path,
            posts_requested=limit_posts,
            source_kind=source_kind,
        )
        return OK, {
            "run_id": run_id,
            "source_mode": outcome.source_mode,
            "status": outcome.status,
            "posts_fetched": len(outcome.posts),
            "posts_parsed": posts_parsed,
            "leads_inserted": inserted,
            "leads_skipped_duplicate": duplicates,
            "raw_response_path": outcome.raw_response_path,
            "error_class": outcome.error_class,
            "error_detail": outcome.error_detail,
        }
    except Exception as exc:  # parser/db system failure; record provenance before exiting
        run_id = db.record_scrape_run(
            settings.db_path,
            started_at=started_at,
            finished_at=db.utc_now_iso(),
            source_mode=outcome.source_mode,
            status="parser_error",
            posts_fetched=len(outcome.posts),
            posts_parsed=posts_parsed,
            leads_inserted=0,
            leads_skipped_duplicate=0,
            error_class=exc.__class__.__name__,
            error_detail=str(exc),
            raw_response_path=outcome.raw_response_path,
        )
        setup_logging(settings).exception("Parser or DB error during scrape: %s", exc.__class__.__name__)
        return SYSTEM_ERROR, {
            "run_id": run_id,
            "source_mode": outcome.source_mode,
            "status": "parser_error",
            "posts_fetched": len(outcome.posts),
            "posts_parsed": posts_parsed,
            "leads_inserted": 0,
            "leads_skipped_duplicate": 0,
            "error_class": exc.__class__.__name__,
            "error_detail": str(exc),
        }


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=__version__, prog_name="garment-leads")
def main() -> None:
    """Scrape and manage garment/textile leads from public group HTML."""


@main.command("init-db")
def init_db_command() -> None:
    """Initialize the SQLite database schema."""

    settings = _settings()
    db.init_db(settings.db_path)
    click.echo(f"Initialized DB: {settings.db_path}")


@main.command("scrape")
@click.option("--no-cache", is_flag=True, help="Fail hard if live scraping fails; do not use cached sample.")
@click.option("--cache-only", is_flag=True, help="Do not attempt live scraping; parse the bundled cached sample.")
@click.option("--limit", type=click.IntRange(min=1), default=None, help="Maximum number of HTML post blocks to fetch/parse (existing v2 flag).")
@click.option(
    "--limit-posts",
    type=click.IntRange(min=1),
    default=None,
    help="Stop the scrape loop once N posts have been parsed and persisted (v3). Distinct from --limit (HTML block cap).",
)
def scrape_command(no_cache: bool, cache_only: bool, limit: int | None, limit_posts: int | None) -> None:
    """Scrape public group HTML and upsert normalized leads."""

    if no_cache and cache_only:
        raise click.ClickException("--no-cache and --cache-only are mutually exclusive")
    settings = _settings()
    db.init_db(settings.db_path)
    cadence_cfg = CadenceConfig(
        min_delay_seconds=settings.fb_min_delay_seconds,
        max_delay_seconds=settings.fb_max_delay_seconds,
        max_requests_per_minute=settings.fb_max_requests_per_minute,
        max_retries=settings.fb_max_retries,
    )
    pacer = Pacer(config=cadence_cfg)
    exit_code, payload = _run_scrape(
        settings,
        no_cache=no_cache,
        cache_only=cache_only,
        limit=limit,
        limit_posts=limit_posts,
        pacer=pacer,
    )
    click.echo(_loud_run_line(str(payload["source_mode"]), str(payload["status"])))
    click.echo(
        "run_id={run_id} posts_fetched={posts_fetched} posts_parsed={posts_parsed} "
        "leads_inserted={leads_inserted} leads_skipped_duplicate={leads_skipped_duplicate}".format(**payload)
    )
    if payload.get("raw_response_path"):
        click.echo(f"raw_response_path={payload['raw_response_path']}")
    if payload.get("error_class"):
        click.echo(f"error={payload['error_class']}: {mask_phone(str(payload.get('error_detail', '')))}", err=True)
    if exit_code != OK:
        raise click.exceptions.Exit(exit_code)


@main.command("scrape-browser")
@click.option(
    "--num-posts", "-n",
    type=click.IntRange(min=1, max=500),
    default=20,
    show_default=True,
    help="Number of posts to scrape from the group feed.",
)
@click.option(
    "--deep-comments/--no-deep-comments",
    default=True,
    show_default=True,
    help="Open each post permalink to fully expand all comments and replies.",
)
@click.option(
    "--cdp-url",
    default="http://127.0.0.1:9222",
    show_default=True,
    help="Chromium CDP endpoint URL.",
)
@click.option(
    "--group-url",
    default=None,
    help="Facebook group URL. Defaults to the configured group.",
)
def scrape_browser_command(
    num_posts: int,
    deep_comments: bool,
    cdp_url: str,
    group_url: str | None,
) -> None:
    """Scrape via Playwright CDP browser (full comment extraction).

    Requires a running Chromium with --remote-debugging-port=9222 and a
    logged-in Facebook session. Uses the two-phase approach:
    1. Scroll the group feed to collect N posts.
    2. Open each post permalink to expand all comments and replies.
    """

    from garment_leads.browser_scraper import scrape_group_browser

    settings = _settings()
    db.init_db(settings.db_path)
    grp_url = group_url or f"https://www.facebook.com/groups/{settings.group_id}"

    click.echo(f"Starting browser scrape: {num_posts} posts, deep_comments={deep_comments}")
    click.echo(f"CDP: {cdp_url}")
    click.echo(f"Group: {grp_url}")

    result = scrape_group_browser(
        cdp_url=cdp_url,
        group_url=grp_url,
        num_posts=num_posts,
        deep_comments=deep_comments,
        raw_html_dir=settings.raw_response_dir,
    )

    if result.error:
        click.echo(f"ERROR: {result.error}", err=True)
        raise click.exceptions.Exit(SYSTEM_ERROR)

    # Persist posts and comments to DB
    started_at = db.utc_now_iso()
    posts_parsed = 0
    leads_inserted = 0
    leads_skipped = 0
    comments_inserted = 0

    for post in result.posts:
        # Upsert post
        post_id, was_new = db.upsert_post(
            settings.db_path,
            post_fb_id=post.post_fb_id,
            permalink=post.permalink,
            author=post.author,
            author_fb_id=post.author_fb_id,
            timestamp_unix=post.timestamp_unix,
            raw_text=post.text,
            source_group_id=settings.group_id,
            source_kind="browser",
        )
        posts_parsed += 1

        # Parse leads from post text
        leads = parse_post_text(
            post.text,
            source_group_id=settings.group_id,
            link=post.permalink,
        )
        if leads:
            ins, dup = db.upsert_leads(settings.db_path, leads)
            leads_inserted += ins
            leads_skipped += dup

        # Insert comments and parse leads from comment text
        for comment in post.comments:
            db.insert_comment(
                settings.db_path,
                post_id=post_id,
                parent_comment_id=None,  # TODO: resolve parent by fb_id
                comment_fb_id=comment.comment_fb_id,
                author=comment.author,
                author_fb_id=comment.author_fb_id,
                comment_text=comment.text,
                timestamp_unix=comment.timestamp_unix,
                is_reply=comment.is_reply,
            )
            comments_inserted += 1

            # Parse leads from comment text — comments often contain
            # phone numbers, atelier offers, and contact info that the
            # post body itself doesn't have.
            comment_leads = parse_post_text(
                comment.text,
                source_group_id=settings.group_id,
                link=post.permalink,
                author=comment.author,
                timestamp_unix=comment.timestamp_unix,
                permalink=post.permalink,
            )
            if comment_leads:
                ins, dup = db.upsert_leads(settings.db_path, comment_leads)
                leads_inserted += ins
                leads_skipped += dup

    # Record scrape run
    source_kind = "browser_authenticated"
    run_id = db.record_scrape_run(
        settings.db_path,
        started_at=started_at,
        finished_at=db.utc_now_iso(),
        source_mode="live",
        status="live_success" if result.error is None else "browser_error",
        posts_fetched=len(result.posts),
        posts_parsed=posts_parsed,
        leads_inserted=leads_inserted,
        leads_skipped_duplicate=leads_skipped,
        raw_response_path=result.raw_html_saved,
        posts_requested=num_posts,
        source_kind=source_kind,
    )

    click.echo(_loud_run_line("live", "browser_success"))
    click.echo(
        f"run_id={run_id} posts={len(result.posts)} posts_parsed={posts_parsed} "
        f"leads_inserted={leads_inserted} leads_skipped={leads_skipped} "
        f"comments={comments_inserted}"
    )
    if result.raw_html_saved:
        click.echo(f"raw_html_saved={result.raw_html_saved}")


@main.command("export")
@click.option("--format", "output_format", type=click.Choice(["csv", "json"]), default="csv", show_default=True)
@click.option("--intent", type=click.Choice(list(VALID_INTENTS)), default=None, help="Filter by intent.")
@click.option("--since", default=None, help="Only export leads last seen on/after YYYY-MM-DD.")
@click.option("--limit", type=click.IntRange(min=1), default=None, help="Maximum rows to export.")
def export_command(output_format: str, intent: str | None, since: str | None, limit: int | None) -> None:
    """Export leads to ./exports as CSV or JSON."""

    if since:
        try:
            datetime.strptime(since, "%Y-%m-%d")
        except ValueError as exc:
            raise click.ClickException("--since must use YYYY-MM-DD") from exc
    settings = _settings()
    path = export_leads(settings.db_path, settings.export_dir, output_format=output_format, intent=intent, since=since, limit=limit)  # type: ignore[arg-type]
    click.echo(f"Exported: {path}")


@main.command("dashboard")
def dashboard_command() -> None:
    """Run the local Flask dashboard on 127.0.0.1:5001 by default."""

    settings = _settings()
    db.init_db(settings.db_path)
    from garment_leads.dashboard import run_dashboard

    run_dashboard(settings)


@main.command("run-all")
@click.option("--no-cache", is_flag=True, help="Fail hard if live scraping fails.")
@click.option("--cache-only", is_flag=True, help="Use only cached sample HTML.")
@click.option("--limit", type=click.IntRange(min=1), default=None, help="Maximum number of posts.")
@click.option("--format", "output_format", type=click.Choice(["csv", "json"]), default="csv", show_default=True)
def run_all_command(no_cache: bool, cache_only: bool, limit: int | None, output_format: str) -> None:
    """Initialize DB, scrape, and export in one command."""

    if no_cache and cache_only:
        raise click.ClickException("--no-cache and --cache-only are mutually exclusive")
    settings = _settings()
    db.init_db(settings.db_path)
    exit_code, payload = _run_scrape(settings, no_cache=no_cache, cache_only=cache_only, limit=limit)
    click.echo(_loud_run_line(str(payload["source_mode"]), str(payload["status"])))
    if exit_code != OK:
        click.echo(f"error={payload.get('error_class')}: {payload.get('error_detail')}", err=True)
        raise click.exceptions.Exit(exit_code)
    export_path = export_leads(settings.db_path, settings.export_dir, output_format=output_format)  # type: ignore[arg-type]
    click.echo(f"Exported: {export_path}")


@main.command("stats")
def stats_command() -> None:
    """Print DB lead and scrape provenance statistics."""

    settings = _settings()
    total = db.count_leads(settings.db_path)
    by_intent = db.counts_by_intent(settings.db_path)
    by_prefix = db.counts_by_phone_prefix(settings.db_path)
    provenance = db.scrape_run_summary(settings.db_path)
    click.echo(f"total_leads={total}")
    click.echo("by_intent=" + json.dumps(by_intent, ensure_ascii=False))
    click.echo("by_phone_prefix=" + json.dumps(by_prefix, ensure_ascii=False))
    click.echo("scrape_provenance=" + json.dumps(provenance, ensure_ascii=False, default=str))


def _fixture_dir() -> Path:
    candidates = [
        Path.cwd() / "tests" / "fixtures" / "posts",
        Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "posts",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


@main.command("validate-fixtures")
def validate_fixtures_command() -> None:
    """Run parser validation against tests/fixtures/posts/*.json."""

    directory = _fixture_dir()
    if not directory.exists():
        raise click.ClickException(f"Fixture directory not found: {directory}")
    failures: list[str] = []
    files = sorted(directory.glob("*.json"))
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        leads = parse_fixture_payload(payload)
        expected_phones = payload.get("expected_phones", [])
        actual_phones = [lead.phone_normalized for lead in leads]
        expected_intent = payload.get("expected_intent")
        actual_intents = {lead.intent for lead in leads} or {"other" if expected_phones else str(expected_intent)}
        expected_leads = int(payload.get("expected_leads", len(expected_phones)))
        passed = sorted(actual_phones) == sorted(expected_phones) and len(leads) == expected_leads
        if expected_leads > 0:
            passed = passed and actual_intents == {expected_intent}
        click.echo(f"{'PASS' if passed else 'FAIL'} {path.name}: phones={actual_phones} intent={sorted(actual_intents)}")
        if not passed:
            failures.append(path.name)
    click.echo(f"fixtures_checked={len(files)} failures={len(failures)}")
    if failures:
        raise click.exceptions.Exit(USER_ERROR)


def _check_dependency(module: str) -> tuple[bool, str]:
    found = importlib.util.find_spec(module) is not None
    return found, f"dependency {module}: {'ok' if found else 'missing'}"


def _check_writable_dir(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, f"write permission {path}: ok"
    except OSError as exc:
        return False, f"write permission {path}: {exc}"


def _check_fb_reachability(settings: Settings) -> tuple[bool, str]:
    try:
        response = requests.get(settings.group_url, timeout=5, headers={"User-Agent": settings.user_agents[0]})
        if response.status_code in {401, 403, 429}:
            return False, f"fb reachability: blocked/login likely (HTTP {response.status_code})"
        return True, f"fb reachability: HTTP {response.status_code}"
    except (requests.RequestException, TimeoutError) as exc:
        return False, f"fb reachability: unavailable ({exc.__class__.__name__})"


@main.command("doctor")
def doctor_command() -> None:
    """Check dependencies, runtime paths, DB connectivity, and Facebook reachability.

    v3: also reports FB cookies status (presence, freshness, fields, validity).
    """

    settings = _settings()
    checks: list[tuple[bool, str]] = []
    for module in ("requests", "bs4", "click", "flask", "phonenumbers", "yaml"):
        checks.append(_check_dependency(module))
    for path in (settings.export_dir, settings.log_dir, settings.raw_response_dir, settings.db_path.parent):
        checks.append(_check_writable_dir(path))
    try:
        db.init_db(settings.db_path)
        with db.connect(settings.db_path) as conn:
            conn.execute("SELECT 1").fetchone()
        checks.append((True, f"sqlite connectivity {settings.db_path}: ok"))
    except Exception as exc:
        checks.append((False, f"sqlite connectivity {settings.db_path}: {exc}"))
    checks.append(_check_fb_reachability(settings))

    # v3: FB cookies check (informational — missing is OK, invalid is hard fail)
    cookie_status = _check_cookies(settings)

    hard_failures = [message for ok, message in checks[:-1] if not ok]
    cookie_failures = [message for ok, message in cookie_status if not ok]
    fb_ok, fb_message = checks[-1]

    for ok, message in checks:
        click.echo(f"{'OK' if ok else 'WARN'} {message}")
    for ok, message in cookie_status:
        click.echo(f"{'OK' if ok else 'WARN'} {message}")

    if cookie_failures:
        # Cookies present but invalid — caller should re-capture
        raise click.exceptions.Exit(SYSTEM_ERROR)
    if hard_failures:
        raise click.exceptions.Exit(SYSTEM_ERROR)
    if not fb_ok:
        click.echo("Doctor completed with FB reachability warning; cached mode should still work.")
        raise click.exceptions.Exit(USER_ERROR)
    raise click.exceptions.Exit(OK)


def _check_cookies(settings: Settings) -> list[tuple[bool, str]]:
    """Return [(ok, message), ...] about the cookie file."""

    path = settings.fb_cookies_path
    lines: list[tuple[bool, str]] = []
    if not path.exists():
        lines.append((True, f"fb cookies {path}: missing (will use anonymous mbasic mode)"))
        return lines
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        lines.append((False, f"fb cookies {path}: unreadable ({exc.__class__.__name__})"))
        return lines
    snapshot = CookieSnapshot.from_json_dict(data)
    problems = validate(snapshot)
    if problems:
        lines.append((False, f"fb cookies {path}: invalid — {'; '.join(problems)}"))
        return lines
    age = freshness_days(snapshot)
    if age is None:
        lines.append((False, f"fb cookies {path}: captured_at missing or unparseable"))
        return lines
    age_str = f"{age:.1f} days"
    if age > settings.fb_cookie_max_age_days:
        lines.append((True, f"fb cookies {path}: stale ({age_str}, max {settings.fb_cookie_max_age_days}d) — re-capture soon"))
    else:
        lines.append((True, f"fb cookies {path}: ok (c_user={mask_c_user(snapshot.c_user)}, age={age_str})"))
    return lines


@main.command("capture-cookies")
@click.option(
    "--chromium-cookies",
    "chromium_cookies",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to Chromium Cookies SQLite DB. Defaults to autodetect (snap, flatpak, native).",
)
@click.option("--locale", default="en_US", show_default=True, help="Locale to record with the cookie snapshot.")
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Destination for the cookie JSON. Defaults to $XDG_CONFIG_HOME/garment-leads/fb_cookies.json.",
)
def capture_cookies_command(
    chromium_cookies: Path | None,
    locale: str,
    output_path: Path | None,
) -> None:
    """Capture FB session cookies from a local Chromium profile.

    Decrypts cookies on Linux using the legacy Chromium 'peanuts' key. If
    decryption fails, the command exits with code 2 and prints instructions
    for capturing cookies manually from DevTools.

    This command never sees your FB password — it only reads the encrypted
    cookies already saved by Chromium after you log in normally.
    """

    target = output_path or default_cookie_path()
    snapshot = capture_from_chromium(db_path=chromium_cookies, locale=locale)
    if snapshot is None:
        click.echo("ERROR: could not capture c_user + xs from the Chromium cookies DB.", err=True)
        click.echo("Possible causes:", err=True)
        click.echo("  - You are not logged into Facebook in Chromium", err=True)
        click.echo("  - The Cookies DB uses an encryption key we cannot derive (e.g. snap keyring locked)", err=True)
        click.echo("  - Path to the DB is wrong (pass --chromium-cookies PATH)", err=True)
        click.echo("Fallback: open chrome://settings/cookies in Chromium, export, or capture via DevTools:", err=True)
        click.echo("  1. Open https://www.facebook.com/ in Chromium (you should be logged in).", err=True)
        click.echo("  2. DevTools > Application > Storage > Cookies > https://www.facebook.com/.", err=True)
        click.echo("  3. Copy values for c_user, xs, fr, datr, sb into the JSON file at:", err=True)
        click.echo(f"     {target}", err=True)
        click.echo('     Example: {"c_user": "1000...4255", "xs": "47:...", "captured_at": "2026-06-19T21:30:00Z"}', err=True)
        raise click.exceptions.Exit(SYSTEM_ERROR)

    problems = validate(snapshot)
    if problems:
        click.echo(f"ERROR: captured snapshot is invalid: {'; '.join(problems)}", err=True)
        raise click.exceptions.Exit(USER_ERROR)

    saved_path = save_cookies(snapshot, path=target)
    age = freshness_days(snapshot)
    click.echo("Captured FB session cookies:")
    click.echo(f"  c_user        : {mask_c_user(snapshot.c_user)}")
    click.echo(f"  xs            : {'set (' + str(len(snapshot.xs)) + ' chars)' if snapshot.xs else 'MISSING'}")
    click.echo(f"  fr            : {'set' if snapshot.fr else '—'}")
    click.echo(f"  datr          : {'set' if snapshot.datr else '—'}")
    click.echo(f"  sb            : {'set' if snapshot.sb else '—'}")
    click.echo(f"  locale        : {snapshot.locale}")
    click.echo(f"  captured_at   : {snapshot.captured_at} (age {age:.2f} days)" if age is not None else f"  captured_at   : {snapshot.captured_at}")
    click.echo(f"  saved to      : {saved_path}")
    click.echo("")
    click.echo("Reminder: re-capture weekly. Stale cookies get rejected by FB.")
    raise click.exceptions.Exit(OK)

if __name__ == "__main__":
    main()
