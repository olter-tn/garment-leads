#!/usr/bin/env bash
set -euo pipefail
cd farbi_app
python scripts/run_migrations_with_lock.py
exec gunicorn -c gunicorn.conf.py wsgi:app
