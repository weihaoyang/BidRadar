from __future__ import annotations

import os

from ..models import Tender
from ..textutil import clean_text, parse_date
from .base import BaseSource, SourceError


class SearxngSource(BaseSource):
    """可选的 SearXNG 全网发现源（需要自建实例并开启 JSON 输出）。"""

    id = "searxng"
    label = "SearXNG 全网搜索（可选）"
    min_interval = 1.0

    def search(
        self,
        keyword: str,
        days: int = 7,
        limit: int = 50,
    ) -> list[Tender]:
        base = os.getenv("SEARXNG_URL", "").rstrip("/")
        if not base:
            raise SourceError(
                "未配置 SEARXNG_URL；请先启动 SearXNG 并在 settings.yml 开启 json 输出"
            )
        if days <= 1:
            time_range = "day"
        elif days <= 7:
            time_range = "week"
        elif days <= 31:
            time_range = "month"
        else:
            time_range = "year"
        params = {
            "q": f"{keyword} (招标 OR 采购 OR 中标 OR 标讯)",
            "format": "json",
            "language": "zh-CN",
            "categories": "general",
            "time_range": time_range,
            "pageno": 1,
        }
        response = self.get(f"{base}/search", params=params)
        try:
            data = response.json()
        except ValueError as exc:
            raise SourceError("SearXNG 返回了非 JSON；请确认 settings.yml 已启用 json") from exc
        results: list[Tender] = []
        for item in data.get("results") or []:
            title = clean_text(item.get("title"))
            url = clean_text(item.get("url"))
            if not title or not url:
                continue
            results.append(
                Tender(
                    source=self.id,
                    external_id=url,
                    title=title,
                    publish_date=parse_date(item.get("publishedDate")),
                    category="web discovery",
                    url=url,
                    summary=clean_text(item.get("content"))[:500] or None,
                    raw=item,
                )
            )
            if len(results) >= limit:
                break
        return results
