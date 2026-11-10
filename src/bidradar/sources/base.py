from __future__ import annotations

from datetime import date, timedelta

from ..http import RateLimiter, make_client, request_with_retry
from ..models import Tender


class SourceError(RuntimeError):
    pass


class BaseSource:
    id = "base"
    label = "Base"
    referer: str | None = None
    min_interval = 1.0

    def __init__(self, timeout: float = 30.0):
        self.client = make_client(referer=self.referer, timeout=timeout)
        self.limiter = RateLimiter(self.min_interval)

    def close(self) -> None:
        self.client.close()

    def get(self, url: str, **kwargs):
        self.limiter.wait()
        return request_with_retry(self.client, "GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        self.limiter.wait()
        return request_with_retry(self.client, "POST", url, **kwargs)

    def search(self, keyword: str, days: int = 7, limit: int = 50) -> list[Tender]:
        raise NotImplementedError

    @staticmethod
    def cutoff(days: int) -> str:
        return (date.today() - timedelta(days=max(days, 0))).isoformat()
