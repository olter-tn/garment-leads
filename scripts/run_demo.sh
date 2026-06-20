#!/usr/bin/env bash
set -euo pipefail
python -m garment_leads.cli init-db
python -m garment_leads.cli scrape --cache-only --limit 10
python -m garment_leads.cli stats
python -m garment_leads.cli export --format csv --limit 10
