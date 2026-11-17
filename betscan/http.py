"""Tiny JSON fetcher with an on-disk cache (stdlib only)."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
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
                  headers: dict | None = None, timeout: int = 30,
                  retry_on: tuple = (429, 500, 502, 503, 504)) -> bytes:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        ttl = self.ttl if ttl is None else ttl
        # Hashed filenames: API keys in query strings never land on disk in clear.
        key = hashlib.sha256(url.encode()).hexdigest()[:24]
        path = self.cache_dir / f"{key}.bin"
        if ttl > 0 and path.exists() and time.time() - path.stat().st_mtime < ttl:
            return path.read_bytes()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    self.last_headers = {k.lower(): v for k, v in resp.headers.items()}
                    body = resp.read()
                break
            except urllib.error.HTTPError as e:
                # Rate limits and server hiccups: back off and retry; anything else is final.
                wait = float(e.headers.get("Retry-After") or 0) or 2 * 3 ** attempt
                if e.code not in retry_on or attempt == 2 or wait > 20:
                    raise   # a long Retry-After (daily quota) means give up, not stall the scan
                time.sleep(wait)
        path.write_bytes(body)
        return body

    def get_json(self, url: str, params: dict | None = None, ttl: int | None = None,
                 headers: dict | None = None, retry_on: tuple = (429, 500, 502, 503, 504)):
        return json.loads(self.get_bytes(url, params, ttl, headers, retry_on=retry_on).decode())
