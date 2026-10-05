#!/usr/bin/env bash
set -euo pipefail
: "${BUNDLE_URL:?BUNDLE_URL is required}"
rm -rf farbi_app /tmp/farbi.zip
mkdir -p farbi_app
python - <<'PY'
import os, urllib.request
urllib.request.urlretrieve(os.environ['BUNDLE_URL'], '/tmp/farbi.zip')
PY
python - <<'PY'
import zipfile
with zipfile.ZipFile('/tmp/farbi.zip') as z:
    z.extractall('farbi_app')
PY
pip install -r farbi_app/requirements/prod.txt
