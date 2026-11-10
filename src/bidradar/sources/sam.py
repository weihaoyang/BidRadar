from __future__ import annotations

import os
from datetime import date, timedelta

from ..models import Tender
from ..textutil import clean_text, parse_amount, parse_date
from .base import BaseSource, SourceError

API = "https://api.sam.gov/opportunities/v2/search"


class SamSource(BaseSource):
    """美国 SAM.gov 合同机会 API（需要免费的 SAM_API_KEY）。"""

    id = "sam"
    label = "US SAM.gov"
    min_interval = 1.5

    def search(
        self,
        keyword: str,
        days: int = 7,
        limit: int = 50,
    ) -> list[Tender]:
        api_key = os.getenv("SAM_API_KEY", "").strip()
        if not api_key:
            raise SourceError("未配置 SAM_API_KEY，跳过 SAM.gov（可在 sam.gov 免费申请）")
        today = date.today()
        start = today - timedelta(days=max(days, 1))
        params = {
            "api_key": api_key,
            "postedFrom": start.strftime("%m/%d/%Y"),
            "postedTo": today.strftime("%m/%d/%Y"),
            "limit": min(max(limit, 1), 1000),
            "offset": 0,
            "title": keyword,
        }
        response = self.get(API, params=params)
        try:
            data = response.json()
        except ValueError as exc:
            raise SourceError("SAM.gov 返回了非 JSON 内容") from exc
        results: list[Tender] = []
        for item in data.get("opportunitiesData") or []:
            title = clean_text(item.get("title"))
            if not title:
                continue
            results.append(
                Tender(
                    source=self.id,
                    external_id=clean_text(item.get("noticeId")) or title,
                    title=title,
                    publish_date=parse_date(item.get("postedDate")),
                    deadline=parse_date(item.get("responseDeadLine")),
                    region=clean_text(item.get("placeOfPerformance")) or None,
                    buyer=clean_text(item.get("fullParentPathName"))
                    or clean_text(item.get("organizationName"))
                    or None,
                    category=clean_text(item.get("type")) or "contract opportunity",
                    amount=parse_amount(item.get("award", {}).get("amount") if isinstance(item.get("award"), dict) else None),
                    currency="USD",
                    url=clean_text(item.get("uiLink")) or None,
                    summary=clean_text(item.get("description"))[:500] or None,
                    raw=item,
                )
            )
            if len(results) >= limit:
                break
        return results
