from __future__ import annotations

import random
import time

import httpx

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class RateLimiter:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed + random.uniform(0, 0.15))
        self._last = time.monotonic()


def make_client(referer: str | None = None, timeout: float = 30.0) -> httpx.Client:
    headers = {
        "User-Agent": DEFAULT_UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/json,application/xhtml+xml,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    return httpx.Client(
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    )


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    retries: int = 3,
    **kwargs,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = client.request(method, url, **kwargs)
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"请求失败: {method} {url}: {last_error}")
