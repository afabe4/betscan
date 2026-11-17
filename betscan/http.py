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

    def get_bytes(self, url: str, params: dict | None = None, ttl: int | None = None,
                  headers: dict | None = None, timeout: int = 30) -> bytes:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        ttl = self.ttl if ttl is None else ttl
        # Hashed filenames: API keys in query strings never land on disk in clear.
        key = hashlib.sha256(url.encode()).hexdigest()[:24]
        path = self.cache_dir / f"{key}.bin"
        if ttl > 0 and path.exists() and time.time() - path.stat().st_mtime < ttl:
            return path.read_bytes()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            self.last_headers = {k.lower(): v for k, v in resp.headers.items()}
            body = resp.read()
        path.write_bytes(body)
        return body

    def get_json(self, url: str, params: dict | None = None, ttl: int | None = None,
                 headers: dict | None = None):
        return json.loads(self.get_bytes(url, params, ttl, headers).decode())
