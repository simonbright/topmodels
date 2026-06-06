"""Shared HTTP client with cache, rate-limit pause, and retries."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx


class CachedHttpClient:
    def __init__(
        self,
        cache_dir: Path,
        *,
        pause_sec: float = 0.35,
        timeout_sec: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self.cache_dir = cache_dir
        self.pause_sec = pause_sec
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self._last_request_at = 0.0
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, namespace: str, url: str, params: dict[str, Any] | None) -> Path:
        payload = json.dumps({"url": url, "params": params or {}}, sort_keys=True)
        digest = hashlib.sha256(payload.encode()).hexdigest()[:24]
        ns_dir = self.cache_dir / namespace
        ns_dir.mkdir(parents=True, exist_ok=True)
        return ns_dir / f"{digest}.json"

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.pause_sec:
            time.sleep(self.pause_sec - elapsed)

    def get_json(
        self,
        namespace: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        refresh: bool = False,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | list[Any]:
        cache_file = self._cache_key(namespace, url, params)
        if cache_file.exists() and not refresh:
            with cache_file.open(encoding="utf-8") as fh:
                cached = json.load(fh)
            return cached.get("body")

        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                self._throttle()
                with httpx.Client(timeout=self.timeout_sec, follow_redirects=True) as client:
                    resp = client.get(url, params=params, headers=headers)
                    resp.raise_for_status()
                    body = resp.json()
                self._last_request_at = time.monotonic()
                cache_file.write_text(
                    json.dumps(
                        {
                            "url": url,
                            "params": params or {},
                            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "body": body,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                return body
            except Exception as exc:  # noqa: BLE001 — retry wrapper
                last_err = exc
                time.sleep(min(2**attempt, 8))
        raise RuntimeError(f"HTTP failed after {self.max_retries} tries: {url}") from last_err
