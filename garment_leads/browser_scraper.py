"""Playwright CDP-based Facebook group scraper.

Two-phase scraping:
  Phase 1 — Feed scroll-loop: scroll the group feed, collect post permalinks
            and basic post data (text, author, timestamp) from each post
            element. Click "See more" on truncated post text.
  Phase 2 — Deep comment loading: for each post permalink, open it in a new
            tab, switch comment sort to "All comments", iteratively click
            "View more comments" / "View N replies" buttons (multilingual),
            click "See more" on truncated comments, then parse the fully
            loaded comment tree.

Inspired by /home/omar/Projects/FBScrapeIdeas but:
  - Uses Playwright connect_over_cdp instead of Selenium WebDriver.
  - Structured dataclasses (PostData, CommentData) instead of loose dicts.
  - Async/await instead of thread pools.
  - SQLite persistence via the existing db module.
  - Proper scope-aware clicking with WeakSet dedup.

Requires a running Chromium with --remote-debugging-port (typically 9222)
and a logged-in Facebook session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from garment_leads import selectors as S
from garment_leads.keywords import (
    EXCLUDE_FOR_COMMENTS,
    EXCLUDE_FOR_SEE_MORE,
    KW_ALL_COMMENTS_SORT,
    KW_CLOSE_HIDE,
    KW_SEE_MORE_TEXT,
    KW_SORT_ALL,
    KW_SORT_CURRENT,
    KW_VIEW_MORE_COMMENTS,
    KW_VIEW_REPLIES,
    REGEX_VIEW_MORE_COMMENTS,
    REGEX_VIEW_REPLIES,
    SORT_EXCLUDES,
    _fold,
)
from garment_leads.timestamp_parser import parse_fb_timestamp, to_unix

logger = logging.getLogger(__name__)

# ---------------- Data structures ----------------


@dataclass(frozen=True)
class CommentData:
    """A single comment or reply extracted from FB HTML."""

    comment_fb_id: str | None
    author: str | None
    author_fb_id: str | None
    text: str
    timestamp: str | None
    timestamp_unix: int | None
    is_reply: bool
    parent_comment_fb_id: str | None
    commenter_profile_pic: str | None


@dataclass(frozen=True)
class PostData:
    """A post extracted from the group feed, with optional deep comments."""

    post_fb_id: str | None
    permalink: str | None
    author: str | None
    author_fb_id: str | None
    text: str
    timestamp: str | None
    timestamp_unix: int | None
    image_url: str | None
    author_profile_pic: str | None
    reactions_count: int | None
    comments_count: int | None
    comments: list[CommentData] = field(default_factory=list)


@dataclass
class BrowserScrapeResult:
    """Result of a full browser scrape run."""

    posts: list[PostData]
    total_comments: int
    raw_html_saved: str | None
    error: str | None = None


# ---------------- JS helpers (as Python strings for page.evaluate) ----------------

# Normalize text: NFKD + strip accents + lowercase + collapse whitespace
_JS_NORM = r"""
const norm = s => (s || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
"""

# Tag the post scope (modal dialog vs standalone) for stable targeting
_JS_RESOLVE_SCOPE = r"""
() => {
    document.querySelectorAll('[data-fb-scope]').forEach(e => e.removeAttribute('data-fb-scope'));
    const dialogs = Array.from(document.querySelectorAll('div[role="dialog"]'));
    function area(el) {
        const r = el.getBoundingClientRect();
        return Math.max(0, r.width) * Math.max(0, r.height);
    }
    let best = null, bestArea = 0;
    for (const d of dialogs) {
        if (!d.querySelector('[role="article"]')) continue;
        const a = area(d);
        if (a > bestArea) { best = d; bestArea = a; }
    }
    if (!best) {
        for (const d of dialogs) {
            const a = area(d);
            if (a > bestArea && a > 200*200) { best = d; bestArea = a; }
        }
    }
    if (best) {
        best.setAttribute('data-fb-scope', 'post');
        return 'dialog';
    }
    const article = document.querySelector('div[role="main"] div[role="article"]');
    if (article) {
        let el = article;
        while (el && el.parentElement && area(el.parentElement) < 1.2 * area(el)) {
            el = el.parentElement;
        }
        el.setAttribute('data-fb-scope', 'post');
        return 'standalone';
    }
    return null;
}
"""

# Click buttons matching keywords (multilingual, accent-insensitive, scoped)
_JS_CLICK_BY_KEYWORDS = r"""
([kws, excl, maxClicks, scopeSel, rxSrc]) => {
    const norm = s => (s || "")
        .normalize("NFKD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase()
        .replace(/\s+/g, " ")
        .trim();
    if (!window.__fbDeepClicked) window.__fbDeepClicked = new WeakSet();
    const seen = window.__fbDeepClicked;
    let root = document;
    if (scopeSel) {
        const scopeEl = document.querySelector(scopeSel);
        if (!scopeEl) return -1;
        root = scopeEl;
    }
    const rxs = (rxSrc || []).map(s => new RegExp(s, "i"));
    const nodes = root.querySelectorAll('[role="button"]');
    let clicks = 0;
    const matched = [];
    for (const el of nodes) {
        if (clicks >= maxClicks) break;
        if (seen.has(el)) continue;
        const rects = el.getClientRects();
        if (rects.length === 0) continue;
        const r = rects[0];
        if (r.width < 4 || r.height < 4) continue;
        if (el.closest('a')) continue;
        const txt = norm((el.innerText || el.textContent || "") + " " + (el.getAttribute("aria-label") || ""));
        if (!txt || txt.length > 200) continue;
        const kwHit = kws.some(k => txt.includes(k));
        const rxHit = rxs.some(rx => rx.test(txt));
        if (!kwHit && !rxHit) continue;
        if (excl.length && excl.some(k => txt.includes(k))) continue;
        matched.push(el);
        seen.add(el);
        clicks++;
    }
    for (const el of matched) {
        try {
            el.scrollIntoView({block: "center", behavior: "instant"});
            el.click();
        } catch (e) {}
    }
    return clicks;
}
"""

# Count comment containers inside scope
_JS_COUNT_COMMENTS = r"""
(scopeSel) => {
    const root = scopeSel ? document.querySelector(scopeSel) : document;
    if (!root) return -1;
    const els = root.querySelectorAll(
        'div[aria-label*="Comment"], div[aria-label*="commentaire"], ' +
        'div[aria-label*="\u062a\u0639\u0644\u064a\u0642"], div[aria-label*="comentario"], ' +
        'div[aria-label*="Kommentar"], ul > li div[role="article"]'
    );
    return els.length;
}
"""

# Scroll within scope (internal scroller or page)
_JS_SCROLL_SCOPE = r"""
(scopeSel) => {
    let target = null;
    if (scopeSel) {
        const dlg = document.querySelector(scopeSel);
        if (!dlg) return -1;
        const all = dlg.querySelectorAll('*');
        let best = null; let bestHeight = 0;
        for (const el of all) {
            const cs = getComputedStyle(el);
            if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll') &&
                el.scrollHeight > el.clientHeight + 50) {
                if (el.scrollHeight > bestHeight) { best = el; bestHeight = el.scrollHeight; }
            }
        }
        target = best || dlg;
    }
    if (target) {
        target.scrollTop = target.scrollHeight;
        return target.scrollHeight;
    } else {
        window.scrollTo(0, document.body.scrollHeight);
        return document.body.scrollHeight;
    }
}
"""

# Check if scope element is still in DOM
_JS_SCOPE_ALIVE = r"""
(scopeSel) => {
    if (!scopeSel) return true;
    return !!document.querySelector(scopeSel);
}
"""

# Switch comment sort dropdown to "All comments"
_JS_OPEN_SORT = r"""
([kws, scopeSel]) => {
    const norm = s => (s || "")
        .normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
        .toLowerCase().replace(/\s+/g, " ").trim();
    const root = scopeSel ? document.querySelector(scopeSel) : document;
    if (!root) return null;
    const buttons = root.querySelectorAll('[role="button"]');
    for (const el of buttons) {
        const rects = el.getClientRects();
        if (rects.length === 0) continue;
        const txt = norm((el.innerText || el.textContent || "") + " " + (el.getAttribute("aria-label") || ""));
        if (!txt || txt.length > 80) continue;
        if (kws.some(k => txt.includes(k))) {
            el.scrollIntoView({block: "center", behavior: "instant"});
            el.click();
            return txt;
        }
    }
    return null;
}
"""

_JS_PICK_SORT_OPTION = r"""
([kws, excludes]) => {
    const norm = s => (s || "")
        .normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
        .toLowerCase().replace(/\s+/g, " ").trim();
    const items = document.querySelectorAll('[role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]');
    for (const el of items) {
        const raw = (el.innerText || el.textContent || "");
        const firstLine = norm(raw.split(/\r?\n/)[0] || "");
        const ariaLabel = norm(el.getAttribute("aria-label") || "");
        const candidates = [firstLine, ariaLabel].filter(Boolean);
        if (!candidates.length) continue;
        const matchesExclude = candidates.some(c => excludes.some(x => c.includes(x)));
        if (matchesExclude) continue;
        const matches = candidates.some(c => kws.some(k => c.includes(k)));
        if (matches) {
            el.click();
            return firstLine || ariaLabel;
        }
    }
    return null;
}
"""

# Dismiss overlay dialogs (login save, notifications, cookie banners)
_JS_DISMISS_OVERLAYS = r"""
() => {
    const xpaths = [
        "//div[@data-testid='dialog']",
        "//div[contains(@role, 'dialog') and contains(@aria-hidden, 'false')]",
        "//div[contains(@aria-label, 'Save your login info') and @role='dialog']",
        "//div[contains(@aria-label, 'Turn on notifications') and @role='dialog']",
    ];
    const dismissTexts = ['Not Now', 'Not now', 'Close', 'Later', 'Dismiss', 'fermer', 'annuler', 'إغلاق'];
    let dismissed = 0;
    for (const xp of xpaths) {
        const els = document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        for (let i = 0; i < els.snapshotLength; i++) {
            const overlay = els.snapshotItem(i);
            if (!overlay.getBoundingClientRect) continue;
            const r = overlay.getBoundingClientRect();
            if (r.width < 10 || r.height < 10) continue;
            const buttons = overlay.querySelectorAll('button, [role="button"], a[aria-label="Close"]');
            for (const btn of buttons) {
                const txt = (btn.innerText || btn.textContent || '').trim();
                if (dismissTexts.some(d => txt.includes(d))) {
                    try { btn.click(); dismissed++; } catch(e) {}
                    break;
                }
            }
        }
    }
    return dismissed;
}
"""

# Get all post elements' outerHTML
_JS_GET_POST_HTMLS = r"""
() => {
    // New approach: find posts by their h2 (author name) element.
    // On www.facebook.com, posts and comments are both div[role="article"],
    // but the POST has an h2 with the author name, and the post TEXT is in
    // a div[dir=auto] that is NOT inside a div[role=article] (which would
    // be a comment). We walk up from h2 to find the feed unit container,
    // then extract the post text, author, permalink, and timestamp.
    const h2s = document.querySelectorAll("h2");
    const seenUrls = new Set();
    const results = [];
    for (const h2 of h2s) {
        const authorLink = h2.querySelector("a[href]");
        if (!authorLink) continue;
        let container = h2.parentElement;
        for (let depth = 0; depth < 10 && container; depth++) {
            const postLink = container.querySelector('a[href*="/posts/"]');
            if (postLink) {
                const baseUrl = postLink.href.split("?")[0];
                if (seenUrls.has(baseUrl)) break;
                seenUrls.add(baseUrl);
                // Build a synthetic post element HTML with the extracted data
                const author = h2.innerText.trim();
                // Get post text: div[dir=auto] NOT inside div[role=article]
                const allDirAuto = container.querySelectorAll("div[dir=auto]");
                let postText = "";
                for (const div of allDirAuto) {
                    if (div.closest('div[role="article"]')) continue;
                    const t = (div.innerText || "").trim();
                    if (t.length < 15) continue;
                    if (t === author) continue;
                    if (/^(Like|Reply|Share|See translation|Comment|Share post)$/i.test(t)) continue;
                    postText = t;
                    break;
                }
                // Get clean permalink (without comment_id)
                const allLinks = container.querySelectorAll('a[href*="/posts/"]');
                let permalink = baseUrl;
                for (const l of allLinks) {
                    if (!l.href.includes("comment_id=")) {
                        permalink = l.href.split("?")[0];
                        break;
                    }
                }
                // Get timestamp
                const abbr = container.querySelector("abbr[title]");
                const timestamp = abbr ? abbr.getAttribute("title") : "";
                // Build synthetic HTML for the BS parser
                const syntheticHtml = '<div class="fb-post">' +
                    '<h2><strong>' + author.replace(/</g, "&lt;") + '</strong></h2>' +
                    '<div data-ad-rendering-role="story_message"><div>' +
                    postText.replace(/</g, "&lt;") + '</div></div>' +
                    (timestamp ? '<abbr title="' + timestamp.replace(/"/g, "&quot;") + '">' + timestamp + '</abbr>' : '') +
                    '<a href="' + permalink + '">permalink</a>' +
                    '</div>';
                results.push(syntheticHtml);
                break;
            }
            container = container.parentElement;
        }
    }
    return results;
}
"""

# Click "See more" on the currently focused post element
_JS_CLICK_SEE_MORE = r"""
(postEl) => {
    const buttons = postEl.querySelectorAll('div[role="button"], a');
    let clicked = 0;
    const seeMoreTexts = ['see more', 'show more', 'voir plus', 'afficher plus',
                          'عرض المزيد', 'mehr anzeigen', 'ver mas', 'ver mais'];
    const norm = s => (s || "").toLowerCase().trim();
    for (const btn of buttons) {
        const txt = norm(btn.innerText || btn.textContent || '');
        if (seeMoreTexts.some(t => txt.includes(t)) && txt.length < 30) {
            try {
                btn.scrollIntoView({block: 'center', behavior: 'instant'});
                btn.click();
                clicked++;
            } catch(e) {}
        }
    }
    return clicked;
}
"""


# ---------------- HTML parsing (BeautifulSoup) ----------------

def _extract_post_from_html(
    html: str,
    post_url: str | None,
    post_id: str | None,
) -> PostData | None:
    """Parse a post's outerHTML into a PostData using BeautifulSoup."""
    soup = BeautifulSoup(html, "html.parser")

    # Author name
    author = None
    el = soup.select_one(S.AUTHOR_NAME_BS)
    if el:
        author = el.get_text(strip=True)

    # Author profile pic
    author_pic = None
    pic_el = soup.select_one(S.AUTHOR_PROFILE_PIC_BS)
    if pic_el:
        if pic_el.name == "image" and pic_el.has_attr("xlink:href"):
            author_pic = pic_el["xlink:href"]
        elif pic_el.name == "img" and pic_el.has_attr("src"):
            author_pic = pic_el["src"]

    # Post text
    text = ""
    text_container = soup.select_one(S.POST_TEXT_CONTAINER_BS)
    if text_container:
        parts = []
        for elem in text_container.find_all(string=False, recursive=False):
            if not elem.find(["button", "a"], attrs={"role": "button"}):
                elem_text = elem.get_text(separator=" ", strip=True)
                if elem_text:
                    parts.append(elem_text)
        if parts:
            text = "\n".join(parts)
        else:
            text = text_container.get_text(separator=" ", strip=True)

    if not text:
        generic = soup.select_one(S.GENERIC_TEXT_DIV_BS)
        if generic:
            text = generic.get_text(separator=" ", strip=True)

    # Post image
    image_url = None
    img_el = soup.select_one(S.POST_IMAGE_BS)
    if img_el:
        if img_el.name == "img" and img_el.has_attr("src"):
            image_url = img_el["src"]
        elif img_el.has_attr("style"):
            match = re.search(r'background-image:\s*url\(["\']?([^)"\']*)["\']?\)', img_el["style"])
            if match:
                image_url = match.group(1)

    # Timestamp
    raw_ts = None
    abbr = soup.select_one(S.POST_TIMESTAMP_ABBR_BS)
    if abbr and abbr.get("title"):
        raw_ts = abbr["title"]

    if not raw_ts:
        time_link = soup.select_one(S.POST_TIMESTAMP_LINK_TEXT_BS)
        if time_link:
            raw_ts = time_link.get_text(strip=True)

    if not raw_ts:
        # Fallback: scan links for parseable timestamps
        for link in soup.select('div[role="article"] a[href*="/posts/"], div[role="article"] a[aria-label]'):
            link_title = link.get("title")
            if link_title and len(link_title) > 5:
                if parse_fb_timestamp(link_title):
                    raw_ts = link_title
                    break
            link_aria = link.get("aria-label")
            if link_aria and len(link_aria) > 5:
                if parse_fb_timestamp(link_aria):
                    raw_ts = link_aria
                    break
            if raw_ts:
                break

    parsed_ts = parse_fb_timestamp(raw_ts) if raw_ts else None
    ts_iso = parsed_ts.isoformat() if parsed_ts else None
    ts_unix = to_unix(parsed_ts) if parsed_ts else None

    # Post ID extraction from URL
    if not post_id and post_url:
        parsed_url = urlparse(post_url)
        path_parts = parsed_url.path.split("/")
        for part_name in ["posts", "videos", "photos", "watch", "story", "permalink"]:
            if part_name in path_parts:
                try:
                    id_candidate = path_parts[path_parts.index(part_name) + 1]
                    if id_candidate and re.match(r"^[a-zA-Z0-9._-]+$", id_candidate):
                        post_id = id_candidate
                        break
                except IndexError:
                    pass

        if not post_id:
            query_params = parse_qs(parsed_url.query)
            for q_param in ["story_fbid", "fbid", "v", "photo_id", "id"]:
                if q_param in query_params and query_params[q_param][0].strip():
                    post_id = query_params[q_param][0]
                    break

        if not post_id:
            id_match = re.search(r"/(\d{10,})/?", parsed_url.path)
            if id_match:
                post_id = id_match.group(1)

    if not post_id:
        post_id = f"gen_{uuid.uuid4().hex[:12]}"

    if not post_url and not post_id:
        return None
    if not text and not author and not ts_iso:
        return None

    return PostData(
        post_fb_id=post_id,
        permalink=post_url,
        author=author,
        author_fb_id=None,
        text=text or "",
        timestamp=ts_iso,
        timestamp_unix=ts_unix,
        image_url=image_url,
        author_profile_pic=author_pic,
        reactions_count=None,
        comments_count=None,
        comments=[],
    )


def _extract_comments_from_html(html: str) -> list[CommentData]:
    """Parse all comments from a fully-loaded post permalink page."""
    soup = BeautifulSoup(html, "html.parser")
    comment_elements = soup.select(S.COMMENT_CONTAINER_BS)
    comments: list[CommentData] = []
    seen_keys: set[str] = set()

    for el in comment_elements:
        # Commenter name
        name = None
        n = el.select_one(S.COMMENTER_NAME_BS)
        if n:
            name = n.get_text(strip=True)

        # Comment text
        text = ""
        t = el.select_one(S.COMMENT_TEXT_PRIMARY_BS)
        if t:
            text = t.get_text(strip=True)
        else:
            cont = el.select_one(S.COMMENT_TEXT_CONTAINER_FALLBACK_BS)
            if cont:
                actual = cont.select_one(S.COMMENT_ACTUAL_TEXT_FALLBACK_BS)
                text = actual.get_text(strip=True) if actual else cont.get_text(strip=True, separator=" ")
            else:
                fb = el.select_one(S.COMMENT_TEXT_FALLBACK_BS)
                if fb:
                    text = fb.get_text(strip=True)

        # Comment FB ID
        comment_fb_id = None
        cid_link = el.select_one(S.COMMENT_ID_LINK_BS)
        if cid_link and cid_link.has_attr("href"):
            qs = parse_qs(urlparse(cid_link["href"]).query)
            if "comment_id" in qs:
                comment_fb_id = qs["comment_id"][0]
        if not comment_fb_id and el.has_attr("data-commentid"):
            comment_fb_id = el["data-commentid"]

        # Commenter profile pic
        pic = None
        pic_el = el.select_one(S.COMMENTER_PROFILE_PIC_BS)
        if pic_el:
            if pic_el.name == "image" and pic_el.has_attr("xlink:href"):
                pic = pic_el["xlink:href"]
            elif pic_el.name == "img" and pic_el.has_attr("src"):
                pic = pic_el["src"]

        # Timestamp
        raw_ts = None
        ts_abbr = el.select_one(S.COMMENT_TIMESTAMP_ABBR_BS)
        if ts_abbr and ts_abbr.get("title"):
            raw_ts = ts_abbr["title"]
        else:
            ts_link = el.select_one(S.COMMENT_TIMESTAMP_LINK_BS)
            if ts_link:
                raw_ts = ts_link.get("aria-label") or ts_link.get_text(strip=True)

        parsed_ts = parse_fb_timestamp(raw_ts) if raw_ts else None
        ts_iso = parsed_ts.isoformat() if parsed_ts else None
        ts_unix = to_unix(parsed_ts) if parsed_ts else None

        # Check if this is a reply (nested under another comment)
        is_reply = False
        parent_id = None
        # Heuristic: reply containers are nested deeper
        parent = el.parent
        while parent:
            if parent.get("aria-label", "").startswith("Reply by"):
                is_reply = True
                break
            parent_comment = parent.select_one(S.COMMENT_ID_LINK_BS)
            if parent_comment and parent_comment != el.select_one(S.COMMENT_ID_LINK_BS):
                parent_qs = parse_qs(urlparse(parent_comment.get("href", "")).query)
                if "comment_id" in parent_qs:
                    parent_id = parent_qs["comment_id"][0]
                    is_reply = True
                    break
            parent = parent.parent

        if not comment_fb_id:
            comment_fb_id = f"fallback_{uuid.uuid4().hex[:10]}"

        # Dedup
        key = comment_fb_id or f"{name}::{text[:200]}"
        if key in seen_keys:
            continue
        if not name and (not text or text == "N/A"):
            continue
        seen_keys.add(key)

        comments.append(CommentData(
            comment_fb_id=comment_fb_id,
            author=name,
            author_fb_id=None,
            text=text,
            timestamp=ts_iso,
            timestamp_unix=ts_unix,
            is_reply=is_reply,
            parent_comment_fb_id=parent_id,
            commenter_profile_pic=pic,
        ))

    return comments


# ---------------- Post URL extraction ----------------

def _extract_post_url_and_id(element_html: str) -> tuple[str | None, str | None]:
    """Extract post URL and ID from a post element's outerHTML."""
    soup = BeautifulSoup(element_html, "html.parser")

    # Try permalink links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(p in href for p in ["/posts/", "/permalink/", "/videos/", "/photos/", "/story"]):
            parsed = urlparse(href)
            if "facebook.com" in parsed.netloc or parsed.netloc == "":
                base = parsed.scheme + "://" + parsed.netloc if parsed.scheme else "https://www.facebook.com"
                url = base + parsed.path
                post_id = None
                path_parts = parsed.path.split("/")
                for part_name in ["posts", "videos", "photos", "watch", "story", "permalink"]:
                    if part_name in path_parts:
                        try:
                            id_candidate = path_parts[path_parts.index(part_name) + 1]
                            if id_candidate and re.match(r"^[a-zA-Z0-9._-]+$", id_candidate):
                                post_id = id_candidate
                                break
                        except IndexError:
                            pass
                if not post_id:
                    query_params = parse_qs(parsed.query)
                    for q_param in ["story_fbid", "fbid", "v", "photo_id", "id"]:
                        if q_param in query_params and query_params[q_param][0].strip():
                            post_id = query_params[q_param][0]
                            break
                if not post_id:
                    id_match = re.search(r"/(\d{10,})/?", parsed.path)
                    if id_match:
                        post_id = id_match.group(1)
                return url, post_id

    return None, None


# ---------------- Main scraper class ----------------


class BrowserScraper:
    """Playwright CDP-based Facebook group scraper.

    Connects to a running Chromium instance via CDP, navigates to a group,
    scrolls the feed to collect posts, and optionally deep-loads comments
    for each post by opening its permalink in a new tab.
    """

    def __init__(
        self,
        cdp_url: str = "http://127.0.0.1:9222",
        group_url: str = "https://www.facebook.com/groups/284751225383775",
        max_scroll_attempts: int = 50,
        max_consecutive_no_posts: int = 3,
        scroll_delay_range: tuple[float, float] = (1.5, 3.5),
        deep_comments: bool = True,
        max_view_more_clicks: int = 60,
        max_reply_clicks: int = 200,
        see_more_in_comments: bool = True,
        raw_html_dir: Path | None = None,
    ) -> None:
        self.cdp_url = cdp_url
        self.group_url = group_url
        self.max_scroll_attempts = max_scroll_attempts
        self.max_consecutive_no_posts = max_consecutive_no_posts
        self.scroll_delay_range = scroll_delay_range
        self.deep_comments = deep_comments
        self.max_view_more_clicks = max_view_more_clicks
        self.max_reply_clicks = max_reply_clicks
        self.see_more_in_comments = see_more_in_comments
        self.raw_html_dir = raw_html_dir

    async def scrape(self, num_posts: int = 20) -> BrowserScrapeResult:
        """Main entry point. Returns a BrowserScrapeResult."""
        from playwright.async_api import async_playwright

        posts: list[PostData] = []
        raw_html_path: str | None = None
        error: str | None = None

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = context.pages[0] if context.pages else await context.new_page()

                # Phase 1: Feed scroll-loop
                posts = await self._scrape_feed(page, num_posts)
                logger.info("Phase 1 complete: collected %s posts from feed", len(posts))

                # Save raw feed HTML
                if self.raw_html_dir:
                    self.raw_html_dir.mkdir(parents=True, exist_ok=True)
                    feed_html = await page.content()
                    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                    raw_path = self.raw_html_dir / f"feed_{ts}.html"
                    raw_path.write_text(feed_html, encoding="utf-8")
                    raw_html_path = str(raw_path)

                # Phase 2: Deep comment loading
                if self.deep_comments and posts:
                    for i, post in enumerate(posts):
                        if not post.permalink:
                            continue
                        logger.info(
                            "Deep comments: post %d/%d — %s",
                            i + 1, len(posts), post.permalink,
                        )
                        comments = await self._fetch_all_comments_for_post(
                            context, post.permalink
                        )
                        # Replace the post with one that has comments
                        posts[i] = PostData(
                            **{**post.__dict__, "comments": comments}
                        )
                        # Polite delay between posts
                        await asyncio.sleep(random.uniform(2.0, 4.0))

                    total_comments = sum(len(p.comments) for p in posts)
                    logger.info(
                        "Phase 2 complete: %s total comments across %s posts",
                        total_comments, len(posts),
                    )
                else:
                    logger.info("Deep comments skipped")

        except Exception as exc:
            logger.error("Browser scrape failed: %s: %s", type(exc).__name__, exc)
            error = f"{type(exc).__name__}: {exc}"

        total_comments = sum(len(p.comments) for p in posts)
        return BrowserScrapeResult(
            posts=posts,
            total_comments=total_comments,
            raw_html_saved=raw_html_path,
            error=error,
        )

    async def _scrape_feed(self, page: Any, num_posts: int) -> list[PostData]:
        """Phase 1: scroll the group feed and collect posts."""
        # Navigate to groups hub first (anti-redirect heuristic)
        try:
            await page.goto("https://www.facebook.com/groups/feed/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
        except Exception as exc:
            logger.debug("Groups hub load failed (non-fatal): %s", exc)

        # Navigate to target group
        await page.goto(self.group_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # Dismiss overlays
        await self._dismiss_overlays(page)

        # Re-navigate if FB redirected away
        for attempt in range(4):
            cur_url = page.url
            if "/groups/" in cur_url and "/feed" not in cur_url.split("?")[0].rstrip("/"):
                break
            logger.info("[attempt %d/4] FB at %s — re-navigating to group", attempt + 1, cur_url)
            await page.goto(self.group_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5 + attempt * 2)

        # Wait for feed
        try:
            await page.wait_for_selector(S.FEED_OR_SCROLLER_CSS, timeout=30000)
        except Exception:
            logger.warning("Feed element not found; trying to continue anyway")

        # Wait for at least one post
        try:
            await page.wait_for_selector(S.POST_CONTAINER_CSS, timeout=30000)
        except Exception:
            logger.error("No post containers found in feed")
            return []

        processed_urls: set[str] = set()
        processed_ids: set[str] = set()
        posts: list[PostData] = []
        scroll_attempt = 0
        consecutive_no_new = 0
        last_count = 0

        while len(posts) < num_posts and scroll_attempt < self.max_scroll_attempts:
            scroll_attempt += 1

            # Scroll to bottom
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            delay = random.uniform(*self.scroll_delay_range)
            await asyncio.sleep(delay)

            # Dismiss any new overlays
            await self._dismiss_overlays(page)

            # Get all post element HTMLs
            post_htmls = await page.evaluate(_JS_GET_POST_HTMLS)
            current_count = len(post_htmls)

            new_posts = current_count - last_count
            if new_posts > 0:
                consecutive_no_new = 0
                logger.info("Scroll %s: %s total elements (+%s new)", scroll_attempt, current_count, new_posts)
            else:
                consecutive_no_new += 1
                logger.debug("Scroll %d: no new posts (consecutive: %d)", scroll_attempt, consecutive_no_new)

            last_count = current_count

            if consecutive_no_new >= self.max_consecutive_no_posts and len(posts) > 0:
                logger.info("No new posts for %d scrolls, have %d — stopping", consecutive_no_new, len(posts))
                break

            # Parse new post elements
            for html in post_htmls:
                if len(posts) >= num_posts:
                    break

                post_url, post_id = _extract_post_url_and_id(html)

                if post_url and post_url in processed_urls:
                    continue
                if post_id and post_id in processed_ids:
                    continue

                # Try clicking "See more" on the post
                # (We do this by evaluating in the page context on the specific element)
                # Since we can't directly reference the DOM element from Python,
                # we rely on the post permalink page for full text.

                post_data = _extract_post_from_html(html, post_url, post_id)
                if post_data is None:
                    continue

                if post_url:
                    processed_urls.add(post_url)
                if post_id:
                    processed_ids.add(post_id)

                posts.append(post_data)
                logger.info("Collected post %s/%s: id=%s", len(posts), num_posts, post_id)

        return posts[:num_posts]

    async def _fetch_all_comments_for_post(
        self, context: Any, post_url: str
    ) -> list[CommentData]:
        """Phase 2: open a post permalink in a new tab, fully expand all
        comments and replies, parse and return them."""
        if not post_url or ("/posts/" not in post_url and "/permalink" not in post_url):
            logger.debug("Skipping deep-comments: not a permalink (%s)", post_url)
            return []

        page = await context.new_page()
        try:
            logger.info("  Opening permalink: %s", post_url)
            await page.goto(post_url, wait_until="domcontentloaded", timeout=30000)

            # Wait for post content to render
            try:
                await page.wait_for_selector(
                    "div[role='dialog'], div[role='main'] div[role='article']",
                    timeout=25000,
                )
            except Exception:
                logger.warning("  Permalink did not render an article in 25s")

            # Let React hydrate
            await asyncio.sleep(5)

            # Resolve scope
            scope_kind = await page.evaluate(_JS_RESOLVE_SCOPE)
            scope_sel = "[data-fb-scope='post']" if scope_kind else None
            logger.info("  Scope: %s", scope_kind or "whole page")

            # Check scope alive
            if scope_sel:
                alive = await page.evaluate(_JS_SCOPE_ALIVE, scope_sel)
                if not alive:
                    logger.warning("  Post scope not alive — bailing")
                    return []

            # Count baseline comments
            baseline = await page.evaluate(_JS_COUNT_COMMENTS, scope_sel)
            logger.info("  Baseline: %s comment containers", max(baseline, 0))

            # 1. Switch sort to "All comments"
            await self._try_switch_to_all_comments(page, scope_sel)
            await asyncio.sleep(1.5)

            # 2. Iteratively expand comments and replies
            prev_count = baseline
            stable_rounds = 0
            for round_idx in range(5):
                # Check scope alive
                if scope_sel:
                    alive = await page.evaluate(_JS_SCOPE_ALIVE, scope_sel)
                    if not alive:
                        logger.warning("  Scope died during round %d", round_idx + 1)
                        break

                # Scroll to load lazy comment chunks
                await self._scroll_within_scope(page, scope_sel, rounds=3)

                # Click "View more comments"
                clicked_more = await self._click_buttons_by_keywords(
                    page,
                    list(KW_VIEW_MORE_COMMENTS),
                    self.max_view_more_clicks,
                    "view-more-comments",
                    scope_sel,
                    list(EXCLUDE_FOR_COMMENTS),
                    list(REGEX_VIEW_MORE_COMMENTS),
                )
                if clicked_more == -1:
                    break

                # Click "View N replies"
                clicked_replies = await self._click_buttons_by_keywords(
                    page,
                    list(KW_VIEW_REPLIES),
                    self.max_reply_clicks,
                    "view-replies",
                    scope_sel,
                    [],
                    list(REGEX_VIEW_REPLIES),
                )
                if clicked_replies == -1:
                    break

                current = await page.evaluate(_JS_COUNT_COMMENTS, scope_sel)
                logger.info(
                    "  Round %s: %s containers (prev %s, +more=%s, +replies=%s)",
                    round_idx + 1, max(current, 0), prev_count, clicked_more, clicked_replies,
                )

                if current <= prev_count and clicked_more == 0 and clicked_replies == 0:
                    stable_rounds += 1
                    if stable_rounds >= 2:
                        break
                else:
                    stable_rounds = 0
                prev_count = current

            # 3. Click "See more" on truncated comments
            if self.see_more_in_comments:
                await self._click_buttons_by_keywords(
                    page,
                    list(KW_SEE_MORE_TEXT),
                    200,
                    "see-more-in-comment",
                    scope_sel,
                    list(EXCLUDE_FOR_SEE_MORE),
                    [],
                )
                await asyncio.sleep(0.8)

            # 4. Parse the fully-loaded page
            page_html = await page.content()
            comments = _extract_comments_from_html(page_html)
            logger.info("  Deep-comments yielded %s unique comments", len(comments))
            return comments

        except Exception as exc:
            logger.error("  Deep-comments failed for %s: %s: %s", post_url, type(exc).__name__, exc)
            return []
        finally:
            await page.close()

    # ---------------- Helper methods ----------------

    async def _dismiss_overlays(self, page: Any) -> None:
        """Dismiss login-save / notification / cookie overlays."""
        try:
            count = await page.evaluate(_JS_DISMISS_OVERLAYS)
            if count:
                logger.debug("Dismissed %s overlay(s)", count)
                await asyncio.sleep(1)
        except Exception:
            pass
        # Also press Escape as a fallback
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass

    async def _scroll_within_scope(self, page: Any, scope_sel: str | None, rounds: int = 3) -> None:
        """Scroll the scope's internal scroller or the page."""
        last = -1
        stable = 0
        for _ in range(rounds):
            try:
                h = await page.evaluate(_JS_SCROLL_SCOPE, scope_sel)
            except Exception:
                return
            if h == -1:
                return
            if h == last:
                stable += 1
                if stable >= 2:
                    return
            else:
                stable = 0
            last = h
            await asyncio.sleep(1.2)

    async def _click_buttons_by_keywords(
        self,
        page: Any,
        keywords: list[str],
        max_clicks: int,
        label: str,
        scope_sel: str | None,
        exclude_keywords: list[str] | None = None,
        regex_patterns: list[str] | None = None,
    ) -> int:
        """Click buttons matching keywords via JS. Returns total clicks, or -1 if scope vanished."""
        norm_kw = [_fold(k) for k in keywords]
        excl = list(exclude_keywords or [])
        excl.extend([_fold(k) for k in KW_CLOSE_HIDE])
        norm_excl = [_fold(k) for k in excl]
        rx = list(regex_patterns or [])

        total = 0
        for _pass in range(6):
            if total >= max_clicks:
                break
            try:
                n = await page.evaluate(
                    _JS_CLICK_BY_KEYWORDS,
                    [norm_kw, norm_excl, max_clicks - total, scope_sel, rx],
                )
            except Exception as exc:
                logger.debug("  JS click pass failed: %s", exc)
                break
            if n == -1:
                logger.warning("  Scope '%s' vanished — aborting '%s'", scope_sel, label)
                return -1
            if not n:
                break
            total += n
            await asyncio.sleep(1.4)
        logger.info("  Expanded %s '%s' button(s)", total, label)
        return total

    async def _try_switch_to_all_comments(self, page: Any, scope_sel: str | None) -> bool:
        """Best-effort switch of the comments sort to 'All comments'."""
        norm_current = [_fold(k) for k in KW_SORT_CURRENT]
        norm_all = [_fold(k) for k in KW_SORT_ALL]

        try:
            opened = await page.evaluate(_JS_OPEN_SORT, [norm_current, scope_sel])
            if not opened:
                logger.info("  Sort dropdown not found — keeping default sort")
                return False
            await asyncio.sleep(1.0)

            sort_excludes = [_fold(k) for k in SORT_EXCLUDES]
            picked = await page.evaluate(_JS_PICK_SORT_OPTION, [norm_all, sort_excludes])
            if picked:
                logger.info("  Switched comment sort to 'All comments' (matched: '%s')", picked[:40])
                await asyncio.sleep(2.0)
                return True
            # Close menu
            try:
                await page.evaluate("document.body.click();")
                await asyncio.sleep(0.3)
            except Exception:
                pass
        except Exception as exc:
            logger.debug("  Sort switch failed: %s", exc)
        return False


# ---------------- Sync wrapper for CLI ----------------

def scrape_group_browser(
    cdp_url: str = "http://127.0.0.1:9222",
    group_url: str = "https://www.facebook.com/groups/284751225383775",
    num_posts: int = 20,
    deep_comments: bool = True,
    raw_html_dir: Path | None = None,
) -> BrowserScrapeResult:
    """Synchronous wrapper for the async BrowserScraper.

    Usage from CLI:
        result = scrape_group_browser(
            cdp_url="http://127.0.0.1:9222",
            group_url="https://www.facebook.com/groups/284751225383775",
            num_posts=50,
            deep_comments=True,
        )
        for post in result.posts:
            ...
    """
    scraper = BrowserScraper(
        cdp_url=cdp_url,
        group_url=group_url,
        deep_comments=deep_comments,
        raw_html_dir=raw_html_dir,
    )
    return asyncio.run(scraper.scrape(num_posts=num_posts))
