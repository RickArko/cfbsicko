#!/usr/bin/env python3
"""Validate Supabase keys in .env without printing secrets."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
key = os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or ""
if not url or not key:
    print("SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY (or ANON) are required", file=sys.stderr)
    sys.exit(1)
if "cfbfantasy" in url:
    print("This looks like the cfbfantasy project. Use a dedicated cfbsicko project.", file=sys.stderr)
    sys.exit(2)

health = f"{url}/auth/v1/health"
try:
    response = requests.get(health, headers={"apikey": key}, timeout=10)
except requests.RequestException as exc:
    print(f"supabase health failed: {exc}", file=sys.stderr)
    sys.exit(1)
if response.status_code >= 400:
    print(f"supabase health HTTP {response.status_code}", file=sys.stderr)
    sys.exit(1)
print(f"supabase ok  project={url.split('//', 1)[-1].split('.', 1)[0]}")
