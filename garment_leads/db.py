"""SQLite persistence and reporting queries for garment-leads."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from garment_leads.parser import ParsedLead

VALID_SOURCE_MODES = ("live", "cached")
VALID_RUN_STATUSES = (
    "live_success",
    "live_failed_cache_used",
    "cache_only",
    "blocked",
    "login_wall",
    "empty_or_markup_changed",
    "network_error",
    "parser_error",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_raw TEXT NOT NULL,
    phone_normalized TEXT NOT NULL UNIQUE,
    name TEXT,
    what TEXT NOT NULL,
    why TEXT NOT NULL,
    intent TEXT NOT NULL,
    intent_confidence REAL NOT NULL DEFAULT 0.0,
    intent_matched_keywords TEXT NOT NULL DEFAULT '[]',
    intent_raw_excerpt TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    post_count INTEGER NOT NULL DEFAULT 1,
    links TEXT NOT NULL DEFAULT '[]',
    source_group_id TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    author TEXT,
    timestamp_unix INTEGER,
    permalink TEXT,
    reactions_count INTEGER,
    comments_count INTEGER
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    source_mode TEXT NOT NULL CHECK(source_mode IN ('live', 'cached')),
    status TEXT NOT NULL CHECK(status IN (
        'live_success',
        'live_failed_cache_used',
        'cache_only',
        'blocked',
        'login_wall',
        'empty_or_markup_changed',
        'network_error',
        'parser_error'
    )),
    posts_fetched INTEGER NOT NULL DEFAULT 0,
    posts_parsed INTEGER NOT NULL DEFAULT 0,
    leads_inserted INTEGER NOT NULL DEFAULT 0,
    leads_skipped_duplicate INTEGER NOT NULL DEFAULT 0,
    error_class TEXT,
    error_detail TEXT,
    raw_response_path TEXT,
    posts_requested INTEGER,
    source_kind TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_fb_id TEXT UNIQUE,
    permalink TEXT,
    author TEXT,
    author_fb_id TEXT,
    timestamp_unix INTEGER,
    raw_text TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    source_group_id TEXT NOT NULL,
    source_kind TEXT
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    parent_comment_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    comment_fb_id TEXT,
    author TEXT,
    author_fb_id TEXT,
    comment_text TEXT NOT NULL,
    timestamp_unix INTEGER,
    is_reply INTEGER NOT NULL DEFAULT 0,
    captured_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leads_phone_normalized ON leads(phone_normalized);
CREATE INDEX IF NOT EXISTS idx_leads_intent ON leads(intent);
CREATE INDEX IF NOT EXISTS idx_leads_source_group_id ON leads(source_group_id);
CREATE INDEX IF NOT EXISTS idx_leads_first_seen ON leads(first_seen);
CREATE INDEX IF NOT EXISTS idx_leads_last_seen ON leads(last_seen);
CREATE INDEX IF NOT EXISTS idx_leads_author ON leads(author);
CREATE INDEX IF NOT EXISTS idx_leads_timestamp_unix ON leads(timestamp_unix);
CREATE INDEX IF NOT EXISTS idx_scrape_runs_started_at ON scrape_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_scrape_runs_status ON scrape_runs(status);
CREATE INDEX IF NOT EXISTS idx_scrape_runs_source_kind ON scrape_runs(source_kind);
CREATE INDEX IF NOT EXISTS idx_posts_post_fb_id ON posts(post_fb_id);
CREATE INDEX IF NOT EXISTS idx_posts_permalink ON posts(permalink);
CREATE INDEX IF NOT EXISTS idx_posts_source_group_id ON posts(source_group_id);
CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent_comment_id ON comments(parent_comment_id);
CREATE INDEX IF NOT EXISTS idx_comments_captured_at ON comments(captured_at);
"""

# Idempotent migrations for v2 -> v3. Each statement uses a try/except-style
# guard inside the function that runs them; SQLite ALTER TABLE doesn't support
# IF NOT EXISTS for columns, so we detect duplicate-column errors.
V3_MIGRATIONS: Final[tuple[str, ...]] = (
    "ALTER TABLE leads ADD COLUMN author TEXT",
    "ALTER TABLE leads ADD COLUMN timestamp_unix INTEGER",
    "ALTER TABLE leads ADD COLUMN permalink TEXT",
    "ALTER TABLE leads ADD COLUMN reactions_count INTEGER",
    "ALTER TABLE leads ADD COLUMN comments_count INTEGER",
    "ALTER TABLE scrape_runs ADD COLUMN posts_requested INTEGER",
    "ALTER TABLE scrape_runs ADD COLUMN source_kind TEXT",
    "CREATE INDEX IF NOT EXISTS idx_leads_author ON leads(author)",
    "CREATE INDEX IF NOT EXISTS idx_leads_timestamp_unix ON leads(timestamp_unix)",
    "CREATE INDEX IF NOT EXISTS idx_scrape_runs_source_kind ON scrape_runs(source_kind)",
)


@dataclass(frozen=True)
class RunRecord:
    """A persisted scrape run record."""

    id: int
    started_at: str
    finished_at: str
    source_mode: str
    status: str
    posts_fetched: int
    posts_parsed: int
    leads_inserted: int
    leads_skipped_duplicate: int
    error_class: str | None
    error_detail: str | None
    raw_response_path: str | None


def utc_now_iso() -> str:
    """Return current UTC time in seconds precision."""

    return datetime.now(UTC).replace(microsecond=0).isoformat()


@contextmanager
def connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with row access by column name."""

    path = Path(db_path)
    if path.parent and str(path.parent) != ".":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _schema_needs_rebuild(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='leads'").fetchone()
    if row is None:
        return False
    columns = {item[1] for item in conn.execute("PRAGMA table_info(leads)").fetchall()}
    required = {"phone_raw", "phone_normalized", "intent", "intent_confidence", "intent_matched_keywords", "intent_raw_excerpt"}
    return not required.issubset(columns)


def init_db(db_path: str | Path) -> None:
    """Create the SQLite schema and indexes, rebuilding an incompatible v1 schema if present.

    Order matters: v3 migrations (column adds) run BEFORE the SCHEMA_SQL executescript
    because the SCHEMA_SQL includes CREATE INDEX statements on v3 columns; if the
    migrations hadn't run yet, those CREATE INDEX calls would fail on a v2 DB.
    """

    with connect(db_path) as conn:
        if _schema_needs_rebuild(conn):
            suffix = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            conn.execute(f"ALTER TABLE leads RENAME TO leads_legacy_{suffix}")
        _apply_v3_migrations(conn)
        conn.executescript(SCHEMA_SQL)


def _apply_v3_migrations(conn: sqlite3.Connection) -> None:
    """Apply v2 -> v3 ALTER TABLE migrations, ignoring duplicate-column errors.

    The list deliberately omits the CREATE INDEX statements — those are already
    in SCHEMA_SQL (with IF NOT EXISTS) and don't need re-application.
    """

    column_migrations = tuple(stmt for stmt in V3_MIGRATIONS if stmt.startswith("ALTER TABLE"))
    for stmt in column_migrations:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            msg = str(exc)
            # Expected on second run (duplicate column) and on fresh DB (no table yet):
            # both are benign because the schema will be created from SCHEMA_SQL below.
            if "duplicate column" in msg or "no such table" in msg:
                continue
            raise


def _json_list(values: Sequence[str]) -> str:
    return json.dumps(list(dict.fromkeys(str(value) for value in values if str(value).strip())), ensure_ascii=False)


def _merge_links(existing_json: str, new_links: Sequence[str]) -> str:
    try:
        existing = json.loads(existing_json) if existing_json else []
    except json.JSONDecodeError:
        existing = []
    if not isinstance(existing, list):
        existing = []
    return _json_list([*(str(item) for item in existing), *new_links])


def _merge_raw_text(existing: str, new: str, max_length: int = 20_000) -> str:
    if not existing:
        merged = new
    elif new in existing:
        merged = existing
    else:
        merged = f"{existing}\n---\n{new}"
    return merged[-max_length:] if len(merged) > max_length else merged


def upsert_lead(db_path: str | Path, lead: ParsedLead) -> bool:
    """Insert or update a lead by normalized E.164 phone.

    Returns True when inserted and False when an existing contact was updated.
    """

    init_db(db_path)
    now = utc_now_iso()
    with connect(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM leads WHERE phone_normalized = ?",
            (lead.phone_normalized,),
        ).fetchone()
        author = getattr(lead, "author", None)
        timestamp_unix = getattr(lead, "timestamp_unix", None)
        permalink = getattr(lead, "permalink", None)
        reactions_count = getattr(lead, "reactions_count", None)
        comments_count = getattr(lead, "comments_count", None)
        if existing is None:
            conn.execute(
                """
                INSERT INTO leads (
                    phone_raw, phone_normalized, name, what, why, intent,
                    intent_confidence, intent_matched_keywords, intent_raw_excerpt,
                    first_seen, last_seen, post_count, links, source_group_id, raw_text,
                    author, timestamp_unix, permalink, reactions_count, comments_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead.phone_raw,
                    lead.phone_normalized,
                    lead.name,
                    lead.what,
                    lead.why,
                    lead.intent,
                    lead.intent_confidence,
                    _json_list(lead.intent_matched_keywords),
                    lead.intent_raw_excerpt,
                    now,
                    now,
                    _json_list(lead.links),
                    lead.source_group_id,
                    lead.raw_text,
                    author,
                    timestamp_unix,
                    permalink,
                    reactions_count,
                    comments_count,
                ),
            )
            return True

        merged_links = _merge_links(str(existing["links"]), lead.links)
        merged_raw_text = _merge_raw_text(str(existing["raw_text"]), lead.raw_text)
        conn.execute(
            """
            UPDATE leads
            SET
                phone_raw = COALESCE(NULLIF(?, ''), phone_raw),
                name = COALESCE(NULLIF(?, ''), name),
                what = COALESCE(NULLIF(?, ''), what),
                why = COALESCE(NULLIF(?, ''), why),
                intent = ?,
                intent_confidence = ?,
                intent_matched_keywords = ?,
                intent_raw_excerpt = ?,
                last_seen = ?,
                post_count = post_count + 1,
                links = ?,
                source_group_id = ?,
                raw_text = ?,
                author = COALESCE(?, author),
                timestamp_unix = COALESCE(?, timestamp_unix),
                permalink = COALESCE(?, permalink),
                reactions_count = COALESCE(?, reactions_count),
                comments_count = COALESCE(?, comments_count)
            WHERE phone_normalized = ?
            """,
            (
                lead.phone_raw,
                lead.name,
                lead.what,
                lead.why,
                lead.intent,
                lead.intent_confidence,
                _json_list(lead.intent_matched_keywords),
                lead.intent_raw_excerpt,
                now,
                merged_links,
                lead.source_group_id,
                merged_raw_text,
                author,
                timestamp_unix,
                permalink,
                reactions_count,
                comments_count,
                lead.phone_normalized,
            ),
        )
        return False


def upsert_leads(db_path: str | Path, leads: Sequence[ParsedLead]) -> tuple[int, int]:
    """Upsert many leads and return (inserted, duplicates_updated)."""

    inserted = 0
    duplicates = 0
    for lead in leads:
        if upsert_lead(db_path, lead):
            inserted += 1
        else:
            duplicates += 1
    return inserted, duplicates


@dataclass(frozen=True)
class PostRecord:
    """A persisted post row."""

    id: int
    post_fb_id: str | None
    permalink: str | None
    author: str | None
    author_fb_id: str | None
    timestamp_unix: int | None
    raw_text: str
    first_seen: str
    last_seen: str
    source_group_id: str
    source_kind: str


@dataclass(frozen=True)
class CommentRecord:
    """A persisted comment row."""

    id: int
    post_id: int
    parent_comment_id: int | None
    comment_fb_id: str | None
    author: str | None
    author_fb_id: str | None
    comment_text: str
    timestamp_unix: int | None
    is_reply: bool
    captured_at: str


def upsert_post(
    db_path: str | Path,
    *,
    post_fb_id: str | None,
    permalink: str | None,
    author: str | None,
    author_fb_id: str | None,
    timestamp_unix: int | None,
    raw_text: str,
    source_group_id: str,
    source_kind: str,
) -> tuple[int, bool]:
    """Insert or update a post, return (post_id, was_inserted).

    Dedup key: post_fb_id when available, else permalink.
    """

    init_db(db_path)
    now = utc_now_iso()
    with connect(db_path) as conn:
        # Try dedup by post_fb_id first
        existing: sqlite3.Row | None = None
        if post_fb_id:
            existing = conn.execute(
                "SELECT id FROM posts WHERE post_fb_id = ?", (post_fb_id,)
            ).fetchone()
        if existing is None and permalink:
            existing = conn.execute(
                "SELECT id FROM posts WHERE permalink = ?", (permalink,)
            ).fetchone()
        if existing is not None:
            pid = int(existing["id"])
            conn.execute(
                """
                UPDATE posts
                SET
                    last_seen = ?,
                    author = COALESCE(?, author),
                    author_fb_id = COALESCE(?, author_fb_id),
                    timestamp_unix = COALESCE(?, timestamp_unix),
                    raw_text = COALESCE(NULLIF(?, ''), raw_text),
                    source_kind = COALESCE(NULLIF(?, ''), source_kind)
                WHERE id = ?
                """,
                (now, author, author_fb_id, timestamp_unix, raw_text, source_kind, pid),
            )
            return pid, False
        cur = conn.execute(
            """
            INSERT INTO posts (
                post_fb_id, permalink, author, author_fb_id, timestamp_unix,
                raw_text, first_seen, last_seen, source_group_id, source_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                post_fb_id,
                permalink,
                author,
                author_fb_id,
                timestamp_unix,
                raw_text,
                now,
                now,
                source_group_id,
                source_kind,
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("SQLite did not return a post id")
        return int(cur.lastrowid), True


def insert_comment(
    db_path: str | Path,
    *,
    post_id: int,
    parent_comment_id: int | None,
    comment_fb_id: str | None,
    author: str | None,
    author_fb_id: str | None,
    comment_text: str,
    timestamp_unix: int | None,
    is_reply: bool,
    captured_at: str | None = None,
) -> int:
    """Insert one comment. Idempotent on comment_fb_id when present."""

    init_db(db_path)
    now = captured_at or utc_now_iso()
    with connect(db_path) as conn:
        if comment_fb_id:
            existing = conn.execute(
                "SELECT id FROM comments WHERE comment_fb_id = ?", (comment_fb_id,)
            ).fetchone()
            if existing is not None:
                return int(existing["id"])
        cur = conn.execute(
            """
            INSERT INTO comments (
                post_id, parent_comment_id, comment_fb_id, author, author_fb_id,
                comment_text, timestamp_unix, is_reply, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                post_id,
                parent_comment_id,
                comment_fb_id,
                author,
                author_fb_id,
                comment_text,
                timestamp_unix,
                1 if is_reply else 0,
                now,
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("SQLite did not return a comment id")
        return int(cur.lastrowid)


def fetch_comments_for_post(db_path: str | Path, post_id: int) -> list[dict[str, Any]]:
    """Return all comments for a post, ordered for natural reading (top-level first, then chronological)."""

    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, post_id, parent_comment_id, comment_fb_id, author, author_fb_id,
                   comment_text, timestamp_unix, is_reply, captured_at
            FROM comments
            WHERE post_id = ?
            ORDER BY (parent_comment_id IS NULL) DESC,
                     COALESCE(timestamp_unix, 0) ASC,
                     id ASC
            """,
            (post_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def count_comments_for_post(db_path: str | Path, post_id: int) -> int:
    """Return total comments+replies stored for a post."""

    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM comments WHERE post_id = ?", (post_id,)
        ).fetchone()
    return int(row["n"] if row else 0)


def record_scrape_run(
    db_path: str | Path,
    *,
    started_at: str,
    finished_at: str,
    source_mode: str,
    status: str,
    posts_fetched: int,
    posts_parsed: int,
    leads_inserted: int,
    leads_skipped_duplicate: int,
    error_class: str | None = None,
    error_detail: str | None = None,
    raw_response_path: str | None = None,
    posts_requested: int | None = None,
    source_kind: str | None = None,
) -> int:
    """Persist a scrape run provenance record and return its id."""

    if source_mode not in VALID_SOURCE_MODES:
        raise ValueError(f"Invalid source_mode: {source_mode}")
    if status not in VALID_RUN_STATUSES:
        raise ValueError(f"Invalid run status: {status}")
    init_db(db_path)
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO scrape_runs (
                started_at, finished_at, source_mode, status, posts_fetched,
                posts_parsed, leads_inserted, leads_skipped_duplicate,
                error_class, error_detail, raw_response_path,
                posts_requested, source_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                started_at,
                finished_at,
                source_mode,
                status,
                posts_fetched,
                posts_parsed,
                leads_inserted,
                leads_skipped_duplicate,
                error_class,
                error_detail[:2000] if error_detail else None,
                raw_response_path,
                posts_requested,
                source_kind,
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("SQLite did not return a scrape run id")
        return int(cur.lastrowid)


def fetch_all_leads(
    db_path: str | Path,
    *,
    intent: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return leads ordered by recency, optionally filtered for export."""

    init_db(db_path)
    where: list[str] = []
    params: list[Any] = []
    if intent:
        where.append("intent = ?")
        params.append(intent)
    if since:
        where.append("date(last_seen) >= date(?)")
        params.append(since)
    sql = """
        SELECT id, phone_raw, phone_normalized, name, what, why, intent,
               intent_confidence, intent_matched_keywords, intent_raw_excerpt,
               first_seen, last_seen, post_count, links, source_group_id, raw_text,
               author, timestamp_unix, permalink, reactions_count, comments_count
        FROM leads
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY datetime(last_seen) DESC, post_count DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


def count_leads(db_path: str | Path) -> int:
    """Return total lead count."""

    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()
        return int(row["n"] if row else 0)


def counts_by_intent(db_path: str | Path) -> list[dict[str, Any]]:
    """Return lead counts grouped by intent."""

    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT intent, COUNT(*) AS count FROM leads GROUP BY intent ORDER BY count DESC, intent ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def counts_by_phone_prefix(db_path: str | Path) -> list[dict[str, Any]]:
    """Return lead counts grouped by Tunisian phone prefix."""

    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT substr(phone_normalized, 5, 2) AS prefix, COUNT(*) AS count
            FROM leads
            WHERE phone_normalized LIKE '+216%'
            GROUP BY prefix
            ORDER BY count DESC, prefix ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def fetch_top_contacts(db_path: str | Path, limit: int = 10) -> list[dict[str, Any]]:
    """Return contacts with highest observed post_count."""

    return fetch_all_leads(db_path, limit=limit)


def fetch_scrape_runs(db_path: str | Path, limit: int = 10) -> list[dict[str, Any]]:
    """Return recent scrape run records."""

    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, started_at, finished_at, source_mode, status, posts_fetched,
                   posts_parsed, leads_inserted, leads_skipped_duplicate,
                   error_class, error_detail, raw_response_path,
                   posts_requested, source_kind
            FROM scrape_runs
            ORDER BY datetime(started_at) DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def scrape_run_summary(db_path: str | Path) -> dict[str, Any]:
    """Return live/cache provenance summary for dashboard and stats."""

    init_db(db_path)
    with connect(db_path) as conn:
        mode_rows = conn.execute(
            "SELECT source_mode, COUNT(*) AS count FROM scrape_runs GROUP BY source_mode"
        ).fetchall()
        last_success = conn.execute(
            """
            SELECT * FROM scrape_runs
            WHERE status IN ('live_success', 'live_failed_cache_used', 'cache_only')
            ORDER BY datetime(started_at) DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        last_run = conn.execute(
            "SELECT * FROM scrape_runs ORDER BY datetime(started_at) DESC, id DESC LIMIT 1"
        ).fetchone()
    return {
        "by_source_mode": [dict(row) for row in mode_rows],
        "last_successful_scrape": dict(last_success) if last_success else None,
        "last_run": dict(last_run) if last_run else None,
    }


def fetch_posts_with_comment_counts(db_path: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    """Return posts with their comment counts, newest first."""

    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.post_fb_id, p.permalink, p.author, p.author_fb_id,
                   p.timestamp_unix, p.raw_text, p.first_seen, p.last_seen,
                   p.source_group_id, p.source_kind,
                   (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comment_count
            FROM posts p
            ORDER BY datetime(p.first_seen) DESC, p.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def count_posts(db_path: str | Path) -> int:
    """Return total number of posts."""

    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM posts").fetchone()
    return int(row["n"] if row else 0)


def count_all_comments(db_path: str | Path) -> int:
    """Return total number of comments (including replies)."""

    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM comments").fetchone()
    return int(row["n"] if row else 0)


def fetch_recent_comments(db_path: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    """Return recent comments with their parent post info, newest first."""

    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.post_id, c.comment_fb_id, c.author, c.author_fb_id,
                   c.comment_text, c.timestamp_unix, c.is_reply, c.captured_at,
                   p.post_fb_id, p.permalink, p.author AS post_author, p.raw_text AS post_text
            FROM comments c
            LEFT JOIN posts p ON c.post_id = p.id
            ORDER BY datetime(c.captured_at) DESC, c.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
