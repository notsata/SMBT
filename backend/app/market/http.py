"""Shared HTTP client: per-host throttling, exponential backoff on 429/5xx/transport errors.
Error messages carry the host and status only, never URLs or params, so API keys cannot reach logs or clients."""

import logging
import random
import threading
import time

import httpx

UA = "SMBT/2.0 (market research terminal)"
RETRY_STATUS = {429, 500, 502, 503, 504}
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx INFO logs full URLs, which include FRED/FMP keys


class ProviderError(Exception):
    def __init__(self, provider: str, msg: str, status: int | None = None):
        super().__init__(f"{provider}: {msg}")
        self.provider, self.status = provider, status


class Http:
    def __init__(self, min_interval_s: float = 0.25, attempts: int = 3, client: httpx.Client | None = None):
        self.min_interval_s = min_interval_s
        self.attempts = attempts
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": UA}, follow_redirects=True)
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _throttle(self, host: str) -> None:
        with self._lock:
            wait = self._last.get(host, 0) + self.min_interval_s - time.monotonic()
            self._last[host] = time.monotonic() + max(wait, 0)
        if wait > 0:
            time.sleep(wait)

    def get(self, provider: str, url: str, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
        host = httpx.URL(url).host
        err, status = "unknown error", None
        for i in range(self.attempts):
            self._throttle(host)
            try:
                r = self.client.get(url, params=params, headers=headers)
            except httpx.TransportError as e:
                err = f"{type(e).__name__} contacting {host}"
            else:
                if r.status_code == 200:
                    return r
                status = r.status_code
                err = {401: "rejected the API key (401)", 403: "plan does not include this data (403)", 404: "not found (404)"}.get(
                    r.status_code, f"HTTP {r.status_code} from {host}")
                if r.status_code not in RETRY_STATUS:
                    break
                ra = r.headers.get("retry-after", "")
                if ra.isdigit():
                    time.sleep(min(int(ra), 30))
                    continue
            if i < self.attempts - 1:
                time.sleep(min(20.0, 2**i + random.random()))
        raise ProviderError(provider, err, status)

    def json(self, provider: str, url: str, params: dict | None = None, headers: dict | None = None):
        return self.get(provider, url, params, headers).json()


_http: Http | None = None


def http() -> Http:
    global _http
    if _http is None:
        from ..config import get_settings

        _http = Http(get_settings().http_min_interval_s)
    return _http


def set_http(h: Http | None) -> None:
    """Tests inject an Http backed by httpx.MockTransport."""
    global _http
    _http = h
