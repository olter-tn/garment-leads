#!/usr/bin/env bash
set -euo pipefail
: "${BUNDLE_KEY:?BUNDLE_KEY is required}"
rm -rf farbi_app /tmp/farbi-preview.b64 /tmp/farbi-preview.enc /tmp/farbi-preview.zip
mkdir -p farbi_app
python - <<'PY'
import os,re
parts=[]
for key,value in os.environ.items():
    if re.fullmatch(r'BUNDLE_\d{2}', key):
        parts.append((key,value))
if not parts:
    raise SystemExit('No BUNDLE_XX payload chunks found')
parts.sort()
with open('/tmp/farbi-preview.b64','w',encoding='ascii') as f:
    for _,value in parts:
        f.write(value)
print(f'assembled {len(parts)} private payload chunks')
PY
base64 -d /tmp/farbi-preview.b64 > /tmp/farbi-preview.enc
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in /tmp/farbi-preview.enc -out /tmp/farbi-preview.zip -pass env:BUNDLE_KEY
python - <<'PY'
import zipfile
with zipfile.ZipFile('/tmp/farbi-preview.zip') as z:
    z.extractall('farbi_app')
PY
pip install -r farbi_app/requirements/prod.txt
