#!/usr/bin/env bash
set -euo pipefail
: "${BUNDLE_KEY:?BUNDLE_KEY is required}"
rm -rf farbi_app /tmp/farbi-preview.zip
mkdir -p farbi_app
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in .farbi_deploy/farbi_preview.enc -out /tmp/farbi-preview.zip -pass env:BUNDLE_KEY
python - <<'PY'
import zipfile
with zipfile.ZipFile('/tmp/farbi-preview.zip') as z:
    z.extractall('farbi_app')
PY
pip install -r farbi_app/requirements/prod.txt
