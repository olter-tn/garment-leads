"""Facebook public group HTML fetching and post extraction."""

from __future__ import annotations

import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from requests import Response, Session

from garment_leads.auth import DEFAULT_AUTH_HEADERS, FACEBOOK_DOMAIN, mobile_group_url
from garment_leads.cadence import Pacer
from garment_leads.config import Settings, ensure_runtime_dirs, mask_phone, setup_logging
from garment_leads.fb_cookies import CookieSnapshot, load_cookies

LOGIN_MARKERS = (
    "login.php",
    "Log in to Facebook",
    "Log Into Facebook",
    "You must log in",
    "تسجيل الدخول",
    "Connectez-vous",
)
BLOCK_MARKERS = (
    "temporarily blocked",
    "You Can't Use This Feature Right Now",
    "rate limit",
    "captcha",
    "checkpoint",
)
POST_SELECTORS = (
    "article",
    "div[data-ft]",
    "div[role='article']",
    "div.story_body_container",
    "div.userContentWrapper",
)


@dataclass(frozen=True)
class ParsedPost:
    """A public post parsed from group HTML."""

    id: str
    text: str
    link: str | None


@dataclass(frozen=True)
class ScrapeOutcome:
    """Fetch/parse outcome before lead extraction and DB upsert."""

    source_mode: str
    status: str
    posts: list[ParsedPost]
    raw_response_path: str | None
    error_class: str | None = None
    error_detail: str | None = None


class ScrapeFailure(RuntimeError):
    """Raised when live scraping fails and no cache fallback is allowed."""

    def __init__(self, status: str, message: str, error_class: str | None = None, raw_response_path: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.error_class = error_class or self.__class__.__name__
        self.raw_response_path = raw_response_path


def _response_path(settings: Settings) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return settings.raw_response_dir / f"facebook_group_{settings.group_id}_{timestamp}.html"


def _save_raw_response(settings: Settings, html: str) -> Path:
    ensure_runtime_dirs(settings)
    path = _response_path(settings)
    path.write_text(html, encoding="utf-8")
    return path


def _detect_failure_status(html: str, status_code: int) -> str | None:
    if status_code in {401, 403, 429}:
        return "blocked"
    lowered = html.casefold()
    if any(marker.casefold() in lowered for marker in BLOCK_MARKERS):
        return "blocked"
    if any(marker.casefold() in lowered for marker in LOGIN_MARKERS):
        return "login_wall"
    # v3 additions: WAP mobile error pages Facebook serves when unauthenticated
    # requests get blocked at the network layer (no JavaScript hydration available).
    if "<title>خطأ</title>" in html or "<title>Error</title>" in lowered:
        return "blocked"
    if "wml" in lowered and "card" in lowered and len(html) < 5000:
        # Tiny WAP card with no real content is almost certainly a block page
        return "blocked"
    return None


def _request_once(session: Session, settings: Settings, url: str) -> Response:
    headers = {
        "User-Agent": random.choice(settings.user_agents),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ar,fr-FR;q=0.9,fr;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
    }
    return session.get(url, headers=headers, timeout=settings.request_timeout_seconds)


def fetch_live_html(
    settings: Settings,
    retries: int = 2,
    cookies: CookieSnapshot | None = None,
    pacer: Pacer | None = None,
) -> tuple[str, str]:
    """Fetch the public Facebook group HTML, saving the raw response to disk.

    v3: when cookies are provided, uses an authenticated session against
    m.facebook.com with polite pacing via the Pacer. Retries on 429 with
    exponential backoff. Returns (html, raw_response_path).
    """

    logger = setup_logging(settings)
    ensure_runtime_dirs(settings)
    last_error: Exception | None = None
    base_url = mobile_group_url(settings.group_id) if cookies is not None else settings.group_url
    with requests.Session() as session:
        if cookies is not None:
            # Use authenticated headers
            session.headers.update(DEFAULT_AUTH_HEADERS)
            session.headers["User-Agent"] = random.choice(settings.user_agents)
            session.cookies.set("c_user", cookies.c_user, domain=FACEBOOK_DOMAIN)
            session.cookies.set("xs", cookies.xs, domain=FACEBOOK_DOMAIN)
            if cookies.fr:
                session.cookies.set("fr", cookies.fr, domain=FACEBOOK_DOMAIN)
            if cookies.datr:
                session.cookies.set("datr", cookies.datr, domain=FACEBOOK_DOMAIN)
            if cookies.sb:
                session.cookies.set("sb", cookies.sb, domain=FACEBOOK_DOMAIN)
        for attempt in range(retries + 1):
            try:
                if pacer is not None:
                    pacer.before_request()
                response = _request_once(session, settings, base_url)
                if response.status_code == 429 and pacer is not None:
                    backoff = pacer.backoff_seconds(attempt)
                    if backoff > 0:
                        logger.warning("429 rate-limited, backing off %ss", int(backoff))
                        pacer.sleep_fn(backoff)
                        continue
                html = response.text or ""
                raw_path = _save_raw_response(settings, html)
                failure_status = _detect_failure_status(html, response.status_code)
                if failure_status:
                    raise ScrapeFailure(
                        failure_status,
                        f"Facebook returned {failure_status} signal with HTTP {response.status_code}",
                        error_class="HTTPBlocked" if failure_status == "blocked" else "LoginWall",
                        raw_response_path=str(raw_path),
                    )
                if response.status_code >= 400:
                    raise ScrapeFailure(
                        "network_error",
                        f"HTTP {response.status_code} while fetching group page",
                        error_class="HTTPError",
                        raw_response_path=str(raw_path),
                    )
                logger.info("Fetched live group HTML from %s", base_url)
                return html, str(raw_path)
            except ScrapeFailure:
                raise
            except requests.RequestException as exc:
                last_error = exc
                logger.warning("Live fetch attempt %s failed: %s", attempt + 1, exc.__class__.__name__)
                if pacer is not None and attempt < retries:
                    pacer.sleep_fn(settings.rate_limit_seconds * (attempt + 1))
                elif attempt < retries:
                    time.sleep(settings.rate_limit_seconds * (attempt + 1))
    detail = str(last_error) if last_error else "unknown network error"
    raise ScrapeFailure("network_error", detail, error_class=last_error.__class__.__name__ if last_error else "NetworkError")


def _element_text(element: object) -> str:
    if not hasattr(element, "get_text"):
        return ""
    text = element.get_text(" ", strip=True)  # type: ignore[attr-defined]
    return " ".join(text.split())


def _element_link(element: object) -> str | None:
    if not hasattr(element, "find"):
        return None
    anchor = element.find("a", href=True)  # type: ignore[attr-defined]
    if anchor is None:
        return None
    href = str(anchor.get("href", ""))
    if not href:
        return None
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"https://mbasic.facebook.com{href}"
    return href


def parse_posts_from_html(html: str, *, limit: int | None = None) -> list[ParsedPost]:
    """Parse post-like text blocks from public Facebook group HTML."""

    soup = BeautifulSoup(html, "html.parser")
    seen_text: set[str] = set()
    posts: list[ParsedPost] = []
    candidates: list[Any] = []
    for selector in POST_SELECTORS:
        candidates.extend(soup.select(selector))
    if not candidates:
        candidates.extend(soup.select("p"))
    if not candidates:
        candidates.extend(soup.select("div"))

    for index, element in enumerate(candidates, start=1):
        text = _element_text(element)
        if len(text) < 18:
            continue
        if text in seen_text:
            continue
        seen_text.add(text)
        post_id = str(getattr(element, "get", lambda _key, _default=None: None)("data-post-id", None) or f"html-{index}")
        posts.append(ParsedPost(id=post_id, text=text, link=_element_link(element)))
        if limit is not None and len(posts) >= limit:
            break
    return posts


def load_cached_posts(settings: Settings, *, limit: int | None = None) -> tuple[list[ParsedPost], str]:
    """Load posts from the bundled cached sample HTML fixture."""

    if not settings.sample_html_path.exists():
        raise ScrapeFailure("network_error", f"Cached sample not found: {settings.sample_html_path}", error_class="CacheMissing")
    html = settings.sample_html_path.read_text(encoding="utf-8")
    posts = parse_posts_from_html(html, limit=limit)
    if not posts:
        raise ScrapeFailure("empty_or_markup_changed", "Cached sample produced no posts", error_class="CacheParseEmpty")
    return posts, str(settings.sample_html_path)


def scrape_group(
    settings: Settings,
    *,
    no_cache: bool = False,
    cache_only: bool = False,
    limit: int | None = None,
    pacer: Pacer | None = None,
) -> ScrapeOutcome:
    """Fetch group posts with explicit live/cache provenance.

    When no_cache is True, live failures raise ScrapeFailure. Otherwise the
    cached sample is used and the returned status is live_failed_cache_used.

    When cookies are present and fresh, the authenticated fetch path is used
    (m.facebook.com with session cookies) instead of mbasic.
    """

    logger = setup_logging(settings)
    if no_cache and cache_only:
        raise ValueError("no_cache and cache_only cannot both be true")
    if cache_only:
        posts, path = load_cached_posts(settings, limit=limit)
        logger.info("Using cached sample only: %s posts", len(posts))
        return ScrapeOutcome(source_mode="cached", status="cache_only", posts=posts, raw_response_path=path)

    cookies = load_cookies(settings.fb_cookies_path, max_age_days=settings.fb_cookie_max_age_days)
    try:
        html, raw_path = fetch_live_html(settings, cookies=cookies, pacer=pacer)
        posts = parse_posts_from_html(html, limit=limit)
        if not posts:
            raise ScrapeFailure(
                "empty_or_markup_changed",
                "Live HTML contained no parseable posts; Facebook markup may have changed or content is unavailable.",
                error_class="MarkupEmpty",
                raw_response_path=raw_path,
            )
        return ScrapeOutcome(source_mode="live", status="live_success", posts=posts, raw_response_path=raw_path)
    except ScrapeFailure as exc:
        logger.error("Live scrape failed [%s]: %s", exc.status, mask_phone(str(exc)))
        if no_cache:
            raise
        posts, path = load_cached_posts(settings, limit=limit)
        return ScrapeOutcome(
            source_mode="cached",
            status="live_failed_cache_used",
            posts=posts,
            raw_response_path=path,
            error_class=exc.error_class,
            error_detail=str(exc),
        )


def posts_to_texts(posts: Sequence[ParsedPost]) -> list[str]:
    """Return post text values from ParsedPost records."""

    return [post.text for post in posts]
