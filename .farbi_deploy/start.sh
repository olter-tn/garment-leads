#!/usr/bin/env bash
set -euo pipefail
cd farbi_app
python - <<'PY'
from application import app, db
with app.app_context():
    db.create_all()
PY
exec gunicorn -c gunicorn.conf.py wsgi:app
