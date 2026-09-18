"""Render the SeaweedFS S3 IAM file from Compose environment variables."""

import json
import os
import sys
from pathlib import Path


target = Path("/config/s3.json")
target.parent.mkdir(parents=True, exist_ok=True)
access_key = os.environ.get("S3_ACCESS_KEY_ID", "").strip()
secret_key = os.environ.get("S3_SECRET_ACCESS_KEY", "").strip()
if bool(access_key) != bool(secret_key):
    raise SystemExit("S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be set together")

# An empty identity list keeps local development usable before photo reports are
# configured. Production must provide both keys: then writes require the backend
# identity while permanent public links are allowed only for object reads.
identities = []
if access_key:
    identities = [
        {
            "name": "techportal-backend",
            "credentials": [{"accessKey": access_key, "secretKey": secret_key}],
            "actions": ["Admin"],
        },
        {"name": "anonymous", "actions": ["Read"]},
    ]
payload = {"identities": identities}
temporary = target.with_suffix(".tmp")
temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
temporary.chmod(0o600)
temporary.replace(target)
