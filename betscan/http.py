"""Tiny JSON fetcher with an on-disk cache (stdlib only)."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "betscan/0.1"


class Fetcher:
    def __init__(self, cache_dir: Path, ttl: int = 600):
        self.cache_dir = cache_dir / "http"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl
        self.last_headers: dict[str, str] = {}

    def get_json(self, url: str, params: dict | None = None, ttl: int | None = None):
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        ttl = self.ttl if ttl is None else ttl
        # Never write API keys into cache filenames.
        key = hashlib.sha256(url.encode()).hexdigest()[:24]
        path = self.cache_dir / f"{key}.json"
        if ttl > 0 and path.exists() and time.time() - path.stat().st_mtime < ttl:
            return json.loads(path.read_text())
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            self.last_headers = {k.lower(): v for k, v in resp.headers.items()}
            data = json.loads(resp.read().decode())
        path.write_text(json.dumps(data))
        return data
