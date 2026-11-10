from __future__ import annotations

from datetime import date, timedelta

from ..models import Tender
from ..textutil import clean_text, flatten, parse_date
from .base import BaseSource, SourceError

API = "https://api.ted.europa.eu/v3/notices/search"

FIELDS = [
    "publication-number",
    "notice-title",
    "buyer-name",
    "publication-date",
    "deadline-date-lot",
    "buyer-country",
    "classification-cpv",
]


class TedSource(BaseSource):
    """EU TED 公开招标检索 API（无需 Key）。"""

    id = "ted"
    label = "EU TED (Tenders Electronic Daily)"
    min_interval = 1.0

    def search(
        self,
        keyword: str,
        days: int = 7,
        limit: int = 50,
        scope: str = "ACTIVE",
    ) -> list[Tender]:
        escaped = keyword.replace('"', '\\"')
        start = (date.today() - timedelta(days=max(days, 0))).strftime("%Y%m%d")
        body = {
            "query": f'FT~"{escaped}" AND PD>={start}',
            "fields": FIELDS,
            "page": 1,
            "limit": min(max(limit, 1), 50),
            "scope": scope,
            "paginationMode": "PAGE_NUMBER",
        }
        response = self.post(API, json=body)
        try:
            data = response.json()
        except ValueError as exc:
            raise SourceError("TED 返回了非 JSON 内容") from exc
        if "error" in data:
            error = data["error"]
            raise SourceError(f"TED 查询失败: {error}")

        cutoff = (date.today() - timedelta(days=max(days, 0))).isoformat()
        results: list[Tender] = []
        for notice in data.get("notices") or []:
            publish_date = parse_date(notice.get("publication-date"))
            if publish_date and publish_date < cutoff:
                continue
            title = clean_text(flatten(notice.get("notice-title")))
            if not title:
                continue
            links = notice.get("links") or {}
            url = (
                (links.get("html") or {}).get("ENG")
                or (links.get("htmlDirect") or {}).get("ENG")
                or f"https://ted.europa.eu/en/notice/{notice.get('publication-number')}"
            )
            results.append(
                Tender(
                    source=self.id,
                    external_id=str(notice.get("publication-number")),
                    title=title,
                    publish_date=publish_date,
                    deadline=parse_date(flatten(notice.get("deadline-date-lot"))),
                    region=flatten(notice.get("buyer-country")) or None,
                    buyer=clean_text(flatten(notice.get("buyer-name"))) or None,
                    category=flatten(notice.get("classification-cpv")) or None,
                    currency="EUR",
                    url=url,
                    summary=None,
                    raw=notice,
                )
            )
            if len(results) >= limit:
                break
        return results
