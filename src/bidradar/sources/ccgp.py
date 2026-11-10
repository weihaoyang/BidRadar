from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Tender
from ..textutil import clean_text, parse_amount, parse_date
from .base import BaseSource, SourceError

SEARCH = "https://search.ccgp.gov.cn/bxsearch"
PORTAL = "https://www.ccgp.gov.cn/"


class CcgpSource(BaseSource):
    """中国政府采购网（HTML 检索，限流较严）。"""

    id = "ccgp"
    label = "中国政府采购网"
    referer = PORTAL
    min_interval = 5.0

    def search(
        self,
        keyword: str,
        days: int = 7,
        limit: int = 50,
        bid_type: str = "0",
    ) -> list[Tender]:
        today = date.today()
        start = today - timedelta(days=max(days, 1))
        params = {
            "searchtype": "1",
            "page_index": "1",
            "bidSort": "0",
            "buyerName": "",
            "projectId": "",
            "pinMu": "0",
            "bidType": bid_type,
            "dbselect": "bidx",
            "kw": keyword,
            "start_time": start.isoformat(),
            "end_time": today.isoformat(),
            "timeType": "6",
            "displayZone": "",
            "zoneId": "",
            "pppStatus": "0",
            "agentName": "",
        }
        response = self.get(SEARCH, params=params)
        text = response.text
        if "访问过于频繁" in text or "访问频繁" in text:
            raise SourceError("ccgp 触发访问频率限制，请降低频率或配置代理")
        if len(text) < 4000 and "搜索结果" not in text:
            raise SourceError("ccgp 返回页面异常（可能被限流或需要验证码）")

        soup = BeautifulSoup(text, "lxml")
        items = soup.select("ul.vT-srch-result-list-bid > li")
        if not items:
            items = soup.select(".vT-srch-result-list-bid li")
        if not items:
            items = self._regex_items(text)

        results: list[Tender] = []
        cutoff = start.isoformat()
        for item in items:
            if isinstance(item, dict):
                title = item["title"]
                href = item["href"]
                publish_date = item.get("date")
                amount = item.get("amount")
                summary = item.get("summary")
            else:
                link = item.find("a")
                if not link:
                    continue
                title = clean_text(link.get_text(" ", strip=True))
                href = urljoin(PORTAL, link.get("href") or "")
                block_text = clean_text(item.get_text(" ", strip=True))
                publish_date = parse_date(block_text)
                amount = parse_amount(None, block_text)
                summary = block_text
            if not title or not href:
                continue
            if publish_date and publish_date < cutoff:
                continue
            results.append(
                Tender(
                    source=self.id,
                    external_id=href,
                    title=title,
                    publish_date=publish_date,
                    region=None,
                    category="政府采购",
                    amount=amount,
                    url=href,
                    summary=summary,
                    raw={"html": summary},
                )
            )
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _regex_items(text: str) -> list[dict]:
        pattern = re.compile(
            r'<li[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)</li>',
            re.S | re.I,
        )
        items = []
        for href, title_html, rest in pattern.findall(text):
            title = clean_text(title_html)
            block = clean_text(rest)
            items.append(
                {
                    "title": title,
                    "href": urljoin(PORTAL, href),
                    "date": parse_date(block),
                    "amount": parse_amount(None, block),
                    "summary": block,
                }
            )
        return items
