"""Authenticated HTTP session builder for the FB mobile scraper."""

from __future__ import annotations

from typing import Final

import requests

from garment_leads.fb_cookies import CookieSnapshot

# Headers that mimic a real mobile Safari/Chrome on iOS talking to m.facebook.com.
# These are conservative defaults; the scraper may override per-request.
DEFAULT_AUTH_HEADERS: Final[dict[str, str]] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Upgrade-Insecure-Requests": "1",
}

# Domain cookies are bound to. Keep as a single string for requests' cookies= param.
FACEBOOK_DOMAIN: Final[str] = ".facebook.com"


def build_authenticated_session(
    cookies: CookieSnapshot,
    user_agent: str,
    extra_headers: dict[str, str] | None = None,
) -> requests.Session:
    """Build a requests.Session pre-configured with FB session cookies + headers.

    The returned session can be used exactly like any other requests.Session for
    hitting https://m.facebook.com/groups/<id>/ and similar authenticated paths.

    The caller is responsible for rate-limit pacing — this function does NOT
    add delays. See garment_leads.cadence for that.
    """

    session = requests.Session()
    headers = dict(DEFAULT_AUTH_HEADERS)
    headers["User-Agent"] = user_agent
    # Locale-aware Accept-Language override
    if cookies.locale:
        # Use the locale's primary language; fall back to en
        primary_lang = cookies.locale.split("_")[0].lower()
        headers["Accept-Language"] = f"{primary_lang}-{cookies.locale.split('_')[-1].upper()},{primary_lang};q=0.9,en;q=0.7"
    if extra_headers:
        headers.update(extra_headers)
    session.headers.update(headers)

    # Bind cookies to .facebook.com so they apply to all FB subdomains
    cookie_dict = {
        "c_user": cookies.c_user,
        "xs": cookies.xs,
    }
    if cookies.fr:
        cookie_dict["fr"] = cookies.fr
    if cookies.datr:
        cookie_dict["datr"] = cookies.datr
    if cookies.sb:
        cookie_dict["sb"] = cookies.sb
    for name, value in cookie_dict.items():
        session.cookies.set(name, value, domain=FACEBOOK_DOMAIN)
    return session


def mobile_group_url(group_id: str) -> str:
    """The logged-in mobile group feed URL."""

    return f"https://m.facebook.com/groups/{group_id}/"