"""Local Flask dashboard for garment-leads.

Shows posts, comments, leads, and scrape provenance with a clean tabbed UI.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Flask, render_template_string, request

from garment_leads import db
from garment_leads.config import Settings, get_settings

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Garment Leads Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    * { box-sizing: border-box; }
    body { font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 0; color: #1f2937; background: #f3f4f6; }
    .header { background: #111827; color: #fff; padding: 1rem 2rem; }
    .header h1 { margin: 0; font-size: 1.4rem; }
    .header .sub { color: #9ca3af; font-size: .8rem; margin-top: .2rem; }
    .tabs { display: flex; gap: 0; background: #1f2937; padding: 0 2rem; }
    .tab { padding: .6rem 1.2rem; color: #9ca3af; cursor: pointer; border: none; background: none; font-size: .9rem; }
    .tab.active { color: #fff; border-bottom: 3px solid #3b82f6; }
    .tab:hover { color: #d1d5db; }
    .content { max-width: 1400px; margin: 0 auto; padding: 1.5rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
    .card { border: 1px solid #d1d5db; border-radius: 12px; padding: 1rem; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
    .provenance { border: 2px solid #111827; background: #f9fafb; }
    .metric { font-size: 1.8rem; font-weight: 700; }
    .metric-label { font-size: .8rem; color: #6b7280; margin-top: .2rem; }
    table { width: 100%; border-collapse: collapse; font-size: .85rem; }
    th { border-bottom: 2px solid #e5e7eb; padding: .5rem; text-align: left; font-weight: 600; color: #374151; }
    td { border-bottom: 1px solid #e5e7eb; padding: .5rem; text-align: left; vertical-align: top; }
    tr:hover { background: #f9fafb; }
    code { background: #f3f4f6; padding: .1rem .3rem; border-radius: 4px; font-size: .8rem; }
    .warn { color: #991b1b; font-weight: 700; }
    .badge { display: inline-block; padding: .15rem .5rem; border-radius: 999px; font-size: .7rem; font-weight: 600; }
    .badge-reply { background: #dbeafe; color: #1e40af; }
    .badge-comment { background: #d1fae5; color: #065f46; }
    .badge-source { background: #fef3c7; color: #92400e; }
    .truncate { max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .truncate:hover { white-space: normal; }
    .page-content { display: none; }
    .page-content.active { display: block; }
    a { color: #2563eb; text-decoration: none; }
    a:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <div class="header">
    <h1>Garment Leads Dashboard</h1>
    <div class="sub">FB group scraping pipeline &mdash; posts, comments, leads, provenance</div>
  </div>
  <div class="tabs">
    <button class="tab active" onclick="showPage('overview')">Overview</button>
    <button class="tab" onclick="showPage('posts')">Posts</button>
    <button class="tab" onclick="showPage('comments')">Comments</button>
    <button class="tab" onclick="showPage('leads')">Leads</button>
    <button class="tab" onclick="showPage('provenance')">Provenance</button>
  </div>

  <div class="content">

  <!-- OVERVIEW -->
  <div id="overview" class="page-content active">
    <div class="grid">
      <div class="card"><div class="metric">{{ total_posts }}</div><div class="metric-label">Posts</div></div>
      <div class="card"><div class="metric">{{ total_comments }}</div><div class="metric-label">Comments</div></div>
      <div class="card"><div class="metric">{{ total_leads }}</div><div class="metric-label">Leads</div></div>
      <div class="card"><div class="metric">{{ total_runs }}</div><div class="metric-label">Scrape runs</div></div>
    </div>

    <div class="grid">
      <div class="card"><h3>By intent</h3><canvas id="intentChart" height="120"></canvas></div>
      <div class="card"><h3>By phone prefix</h3><canvas id="prefixChart" height="120"></canvas></div>
    </div>

    {% if provenance.last_run %}
    <div class="card provenance">
      <h3>Last scrape run</h3>
      <p><strong>Status:</strong> <code>{{ provenance.last_run.status }}</code> &nbsp; <strong>Source:</strong> <span class="badge badge-source">{{ provenance.last_run.source_kind or provenance.last_run.source_mode }}</span></p>
      <p><strong>Started:</strong> {{ provenance.last_run.started_at }} &nbsp; <strong>Finished:</strong> {{ provenance.last_run.finished_at }}</p>
      <p>Posts fetched: <strong>{{ provenance.last_run.posts_fetched }}</strong>, parsed: <strong>{{ provenance.last_run.posts_parsed }}</strong>, leads inserted: <strong>{{ provenance.last_run.leads_inserted }}</strong></p>
      {% if provenance.last_run.error_class %}<p class="warn">Error: {{ provenance.last_run.error_class }} &mdash; {{ provenance.last_run.error_detail }}</p>{% endif %}
    </div>
    {% endif %}
  </div>

  <!-- POSTS -->
  <div id="posts" class="page-content">
    <div class="card">
      <h3>Posts ({{ total_posts }} total, showing {{ posts|length }})</h3>
      <table>
        <thead><tr><th>ID</th><th>FB ID</th><th>Author</th><th>Text</th><th>Comments</th><th>Source</th><th>First seen</th><th>Permalink</th></tr></thead>
        <tbody>
        {% for p in posts %}
        <tr>
          <td>{{ p.id }}</td>
          <td><code>{{ p.post_fb_id[:20] if p.post_fb_id else '-' }}</code></td>
          <td>{{ p.author or '-' }}</td>
          <td class="truncate">{{ p.raw_text[:80] if p.raw_text else '' }}</td>
          <td><strong>{{ p.comment_count }}</strong></td>
          <td><span class="badge badge-source">{{ p.source_kind or '-' }}</span></td>
          <td>{{ p.first_seen[:19] if p.first_seen else '-' }}</td>
          <td>{% if p.permalink %}<a href="{{ p.permalink }}" target="_blank">link</a>{% else %}-{% endif %}</td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  <!-- COMMENTS -->
  <div id="comments" class="page-content">
    <div class="card">
      <h3>Comments ({{ total_comments }} total, showing {{ comments|length }})</h3>
      <table>
        <thead><tr><th>Type</th><th>Author</th><th>Text</th><th>Post</th><th>Post author</th><th>Captured</th></tr></thead>
        <tbody>
        {% for c in comments %}
        <tr>
          <td>{% if c.is_reply %}<span class="badge badge-reply">Reply</span>{% else %}<span class="badge badge-comment">Comment</span>{% endif %}</td>
          <td>{{ c.author or '-' }}</td>
          <td class="truncate">{{ c.comment_text[:100] if c.comment_text else '' }}</td>
          <td class="truncate">{{ c.post_text[:50] if c.post_text else '-' }}</td>
          <td>{{ c.post_author or '-' }}</td>
          <td>{{ c.captured_at[:19] if c.captured_at else '-' }}</td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  <!-- LEADS -->
  <div id="leads" class="page-content">
    <div class="card">
      <h3>Top contacts ({{ total_leads }} total leads)</h3>
      <table>
        <thead><tr><th>Phone</th><th>Name</th><th>Intent</th><th>Count</th><th>What</th><th>Why</th></tr></thead>
        <tbody>
        {% for lead in top_contacts %}
        <tr>
          <td><code>{{ lead.phone_normalized }}</code></td>
          <td>{{ lead.name or '' }}</td>
          <td>{{ lead.intent }}</td>
          <td>{{ lead.post_count }}</td>
          <td class="truncate">{{ lead.what[:60] if lead.what else '' }}</td>
          <td class="truncate">{{ lead.why[:60] if lead.why else '' }}</td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  <!-- PROVENANCE -->
  <div id="provenance" class="page-content">
    <div class="card">
      <h3>Scrape runs ({{ total_runs }} total)</h3>
      <table>
        <thead><tr><th>ID</th><th>Started</th><th>Status</th><th>Source</th><th>Posts</th><th>Parsed</th><th>Leads</th><th>Errors</th></tr></thead>
        <tbody>
        {% for run in runs %}
        <tr>
          <td>{{ run.id }}</td>
          <td>{{ run.started_at[:19] if run.started_at else '-' }}</td>
          <td><code>{{ run.status }}</code></td>
          <td><span class="badge badge-source">{{ run.source_kind or run.source_mode }}</span></td>
          <td>{{ run.posts_fetched }}</td>
          <td>{{ run.posts_parsed }}</td>
          <td>{{ run.leads_inserted }}</td>
          <td>{% if run.error_class %}<span class="warn">{{ run.error_class }}</span>{% else %}-{% endif %}</td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  </div>

<script>
function showPage(id) {
  document.querySelectorAll('.page-content').forEach(e => e.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(e => e.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  event.target.classList.add('active');
}
const byIntent = {{ by_intent_json|safe }};
const byPrefix = {{ by_prefix_json|safe }};
if (byIntent.length) new Chart(document.getElementById('intentChart'), {
  type: 'bar', data: { labels: byIntent.map(x => x.intent), datasets: [{ label: 'Leads', data: byIntent.map(x => x.count), backgroundColor: '#3b82f6' }] },
  options: { responsive: true, plugins: { legend: { display: false } } }
});
if (byPrefix.length) new Chart(document.getElementById('prefixChart'), {
  type: 'bar', data: { labels: byPrefix.map(x => x.prefix), datasets: [{ label: 'Leads', data: byPrefix.map(x => x.count), backgroundColor: '#10b981' }] },
  options: { responsive: true, plugins: { legend: { display: false } } }
});
</script>
</body>
</html>
"""


def create_app(db_path: str | Path | None = None) -> Flask:
    """Create and configure the Flask dashboard app."""

    settings = get_settings()
    database_path = Path(db_path) if db_path is not None else settings.db_path
    app = Flask(__name__)

    @app.get("/")
    def index() -> str:
        total_leads = db.count_leads(database_path)
        total_posts = db.count_posts(database_path)
        total_comments = db.count_all_comments(database_path)
        by_intent = db.counts_by_intent(database_path)
        by_prefix = db.counts_by_phone_prefix(database_path)
        provenance = db.scrape_run_summary(database_path)
        top_contacts = db.fetch_top_contacts(database_path, limit=20)
        posts = db.fetch_posts_with_comment_counts(database_path, limit=50)
        comments = db.fetch_recent_comments(database_path, limit=100)
        runs = db.fetch_scrape_runs(database_path, limit=20)
        return render_template_string(
            HTML_TEMPLATE,
            total_leads=total_leads,
            total_posts=total_posts,
            total_comments=total_comments,
            total_runs=len(runs),
            by_intent=by_intent,
            by_prefix=by_prefix,
            by_intent_json=json.dumps(by_intent, ensure_ascii=False),
            by_prefix_json=json.dumps(by_prefix, ensure_ascii=False),
            provenance=provenance,
            top_contacts=top_contacts,
            posts=posts,
            comments=comments,
            runs=runs,
        )

    return app


def run_dashboard(settings: Settings | None = None) -> None:
    """Run the local dashboard with safe loopback binding by default."""

    current = settings or get_settings()
    if current.flask_host != "127.0.0.1":
        logging.getLogger("garment_leads").warning(
            "FLASK_HOST is overridden to %s; dashboard may be reachable beyond this machine.",
            current.flask_host,
        )
        print(f"WARNING: FLASK_HOST={current.flask_host} exposes the dashboard beyond 127.0.0.1")
    app = create_app(current.db_path)
    app.run(host=current.flask_host, port=current.flask_port)
