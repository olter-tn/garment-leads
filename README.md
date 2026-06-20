# garment-leads

A Python 3.11+ command-line tool for extracting garment/textile business leads from Facebook groups. It scrapes the Tunisian group **الخياطة و التفصيل و الموضة** (`group_id=284751225383775`) using Playwright browser automation, extracts phone numbers, classifies intent (atelier, factory, subcontractor, fabric supplier, job offer), persists everything to SQLite, and serves a local dashboard.

## Features

- **Two-phase browser scraping** via Playwright CDP (connect to a running Chromium):
  - Phase 1: Scroll the group feed, collect posts (author, text, permalink, timestamp)
  - Phase 2: Open each post permalink, switch comment sort to "All comments", expand all "View more comments" / "View N replies" buttons (multilingual), expand "See more" on truncated comments, parse the full comment tree
- **Multilingual keyword matching** (EN/FR/ES/AR/DE/PT/IT) with accent folding for comment expansion buttons
- **Tunisian Darija intent classification** — handles both Arabic script and Latin transliteration (ar3abizi)
- **Structured data** — `PostData` and `CommentData` dataclasses, SQLite with posts, comments, leads, and scrape_runs tables
- **Lead extraction from both posts AND comments** — phone numbers in comments are captured
- **Local Flask dashboard** with tabbed UI: Overview, Posts, Comments, Leads, Provenance
- **Cached HTML fallback** — if live scraping fails, a bundled sample HTML is used
- **153 tests** — dataclasses, URL extraction, HTML parsing, keyword matching, intent classification, DB operations

## Quick start

### Prerequisites

1. Python 3.11+
2. A running Chromium with `--remote-debugging-port=9222` and a logged-in Facebook session

```bash
# Start Chromium (snap or system)
chromium --remote-debugging-port=9222 --user-data-dir=~/snap/chromium/common/chromium --no-first-run &
```

2. Log into Facebook in that Chromium window (only needed once — the session persists).

### Install

```bash
git clone https://github.com/omargassab/garment-leads.git
cd garment-leads
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium  # only needed if not using system Chromium
```

### Usage

```bash
# Initialize the database
garment-leads init-db

# Scrape 5 posts with full comment extraction
garment-leads scrape-browser -n 5 --deep-comments

# Scrape 50 posts, no deep comments (feed only)
garment-leads scrape-browser -n 50 --no-deep-comments

# Legacy HTTP-based scrape (no browser, cached fallback)
garment-leads scrape --cache-only

# Export leads to CSV
garment-leads export --format csv

# Run the dashboard (http://127.0.0.1:5001)
garment-leads dashboard

# Show stats
garment-leads stats
```

## Architecture

```
+-----------------------------+
| garment-leads CLI           |
| scrape-browser / scrape     |
| export / dashboard / stats  |
+-------------+---------------+
              |
              v
+-------------+---------------+     +---------------------------+
| browser_scraper.py          |     | keywords.py               |
| Playwright CDP              |     | 77 multilingual keywords  |
| Phase 1: feed scroll        |     | accent folding            |
| Phase 2: deep comments      |     +---------------------------+
|   - sort to "All comments"  |
|   - expand view-more/replies|
|   - expand see-more         |
|   - parse comment tree      |
+-------------+---------------+
              |
              v
+-------------+---------------+     +---------------------------+
| parser.py                  |<----| data/intent_rules.yml     |
| phonenumbers (TN fallback) |     | AR / TN / FR / EN rules  |
| Arabic-Indic digits        |     +---------------------------+
| intent classification      |
+-------------+---------------+
              |
              v
+-------------+---------------+
| db.py                       |
| SQLite: leads, posts,       |
|   comments, scrape_runs     |
+-------------+---------------+
              |
              v
+-------------+---------------+
| dashboard.py                |
| Flask: Overview/Posts/      |
|   Comments/Leads/Provenance |
+-----------------------------+
```

## Key modules

| Module | Lines | Description |
|--------|-------|-------------|
| `browser_scraper.py` | 1136 | Playwright CDP scraper — feed scroll + deep comment expansion |
| `parser.py` | 560 | Phone extraction (TN), intent classification (YAML rules), name/what/why extraction |
| `db.py` | 789 | SQLite schema, CRUD for leads/posts/comments/scrape_runs |
| `cli.py` | 646 | Click CLI: scrape-browser, scrape, export, dashboard, stats, doctor |
| `selectors.py` | 180 | FB DOM selectors (author, text, comments, timestamps, overlays) |
| `keywords.py` | 207 | 77 multilingual keywords for comment expansion buttons |
| `timestamp_parser.py` | 57 | dateparser-based FB timestamp parsing (relative + absolute) |
| `dashboard.py` | 200 | Flask dashboard with 5 tabs and Chart.js charts |
| `scraper.py` | 308 | Legacy HTTP-based scraper (requests + BeautifulSoup) |
| `fb_cookies.py` | 290 | FB session cookie capture from Chromium |
| `config.py` | 164 | Settings, env vars, logging |
| `auth.py` | 70 | Auth headers for m.facebook.com |
| `cadence.py` | 122 | Rate limiting / pacer |

## How it works — deep comments

1. **Open post permalink** in a new browser tab
2. **Resolve scope** — detect if the post is a modal dialog or standalone page, tag it with `data-fb-scope="post"`
3. **Switch comment sort** to "All comments" (multilingual: "tous les commentaires", "كل التعليقات", etc.) — this ensures ALL comments are shown, not just "Most relevant"
4. **Iteratively expand** (up to 5 rounds):
   - Scroll the dialog's internal scroller to load lazy comment chunks
   - Click "View more comments" buttons (multilingual keyword + regex matching)
   - Click "View N replies" buttons (regex patterns for "View 3 replies", "Voir les 3 réponses", etc.)
   - Stop when the comment count stabilizes for 2 consecutive rounds
5. **Click "See more"** on truncated long comments
6. **Parse** the fully-loaded page with BeautifulSoup — extract commenter name, text, FB comment ID, timestamp, profile pic, reply nesting

## Intent classification

The YAML rules file (`garment_leads/data/intent_rules.yml`) contains weighted keyword rules in 4 language sections:

- **`ar`**: Modern Standard Arabic (خياطة, مصنع, مناولة, قماش)
- **`tn`**: Tunisian Darija (مرحبا بيك, انخيط, موداليست, na5demlek, marhba)
- **`fr`**: French (atelier, couture, sous-traitance, tissu, disponible)
- **`en`**: English (atelier, factory, subcontract, fabric)

Each rule has a weight, keywords, and optional exclude_keywords. The classifier scores all rules, picks the highest-scoring intent, and returns matched keywords + a text excerpt as evidence.

## Configuration

Environment variables (see `.env.example`):

```
GARMENT_LEADS_GROUP_ID=284751225383775
GARMENT_LEADS_DB_PATH=./garment_leads.sqlite3
FLASK_HOST=127.0.0.1
FLASK_PORT=5001
```

## Testing

```bash
pytest --cov=garment_leads --cov-report=term-missing
```

153 tests covering: dataclasses, URL extraction, post HTML parsing, comment HTML parsing, keyword matching (all 7 languages + accent folding), intent classification, DB operations.

## License

MIT