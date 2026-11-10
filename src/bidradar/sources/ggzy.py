from __future__ import annotations

from datetime import date
from typing import Any

from ..models import Tender
from ..textutil import clean_text, parse_amount, parse_date
from .base import BaseSource, SourceError

API = "https://www.ggzy.gov.cn/information/pubTradingInfo/getTradList"
HIS_API = "https://www.ggzy.gov.cn/his/information/pubTradingInfo/getTradList"
PORTAL = "https://www.ggzy.gov.cn/deal/dealList.html"
HIS_PORTAL = "https://www.ggzy.gov.cn/history/dealList.html"

CLASSIFY = {
    "engineering": "01",
    "procurement": "02",
    "land": "03",
    "mining": "04",
    "property": "05",
    "carbon": "21",
    "emission": "22",
    "drug": "23",
    "vaccine": "24",
    "forest": "25",
    "other": "90",
}


class GgzySource(BaseSource):
    """全国公共资源交易平台（国家信息中心聚合接口）。"""

    id = "ggzy"
    label = "全国公共资源交易平台"
    referer = PORTAL
    min_interval = 1.2

    def search(
        self,
        keyword: str,
        days: int = 7,
        limit: int = 50,
        classify: str = "engineering",
    ) -> list[Tender]:
        classes = self._resolve_classes(classify)
        deal_time = self._deal_time(days)
        cutoff = self.cutoff(days)
        results: list[Tender] = []
        per_class_limit = max(1, (limit + len(classes) - 1) // max(len(classes), 1))

        for deal_classify in classes:
            page = 1
            collected = 0
            while collected < per_class_limit and page <= 20:
                payload = {
                    "DEAL_CLASSIFY": deal_classify,
                    "DEAL_TIME": deal_time,
                    "PAGENUMBER": page,
                    "FINDTXT": keyword,
                }
                body = self._request(API, payload, referer=PORTAL)
                records = body.get("records") or []
                if not records:
                    break
                for item in records:
                    publish_date = parse_date(item.get("publishTime"))
                    if publish_date and publish_date < cutoff:
                        continue
                    results.append(self._map(item))
                    collected += 1
                    if collected >= per_class_limit:
                        break
                if page >= int(body.get("pages") or 1):
                    break
                page += 1

        return self._dedupe(results)[:limit]

    def search_range(
        self,
        start: str,
        end: str,
        keyword: str = "",
        classify: str = "all",
        max_pages: int = 50,
        page_size: int = 20,
    ) -> list[Tender]:
        """按日期区间抓取；一年以前的数据走 /his/ 历史接口。"""
        classes = self._resolve_classes(classify)
        one_year_ago = date.today().replace(year=date.today().year - 1)
        use_history = date.fromisoformat(end) < one_year_ago
        endpoint = HIS_API if use_history else API
        referer = HIS_PORTAL if use_history else PORTAL
        results: list[Tender] = []

        for deal_classify in classes:
            page = 1
            total_pages = 1
            while page <= min(max_pages, total_pages):
                payload = {
                    "DEAL_CLASSIFY": deal_classify,
                    "DEAL_TIME": "06",
                    "TIMEBEGIN": start,
                    "TIMEEND": end,
                    "PAGENUMBER": page,
                    "FINDTXT": keyword,
                }
                body = self._request(endpoint, payload, referer=referer)
                records = body.get("records") or []
                if not records:
                    break
                results.extend(self._map(item) for item in records)
                total_pages = int(body.get("pages") or 1)
                page += 1
        return self._dedupe(results)

    # ---- 内部 ----
    def _request(self, endpoint: str, payload: dict[str, Any], referer: str) -> dict:
        response = self.post(endpoint, data=payload, headers={"Referer": referer})
        try:
            data = response.json()
        except ValueError as exc:
            raise SourceError("ggzy 返回了非 JSON 内容（可能被限流）") from exc
        code = data.get("code")
        if code == 800:
            raise SourceError("ggzy 访问频率过高，已被限流，请降低频率或稍后重试")
        if code == 829:
            raise SourceError("ggzy 要求验证码，请稍后重试或使用浏览器抓取")
        if code != 200:
            raise SourceError(f"ggzy 查询失败: {data.get('message') or code}")
        return data.get("data") or {}

    def _map(self, item: dict) -> Tender:
        title = clean_text(item.get("title"))
        publish_date = parse_date(item.get("publishTime"))
        external_id = clean_text(item.get("id")) or f"{publish_date}:{title}"
        relative_url = clean_text(item.get("url"))
        url = (
            f"https://www.ggzy.gov.cn{relative_url}"
            if relative_url.startswith("/")
            else relative_url
        )
        parts = [
            clean_text(item.get("businessTypeText")),
            clean_text(item.get("informationTypeText")),
        ]
        region = " ".join(
            part
            for part in (
                clean_text(item.get("provinceText")),
                clean_text(item.get("cityText")),
            )
            if part
        )
        summary = " / ".join(
            part
            for part in (
                clean_text(item.get("industryTypeText")),
                clean_text(item.get("transactionSourcesPlatformText")),
            )
            if part
        )
        return Tender(
            source=self.id,
            external_id=external_id,
            title=title,
            publish_date=publish_date,
            deadline=parse_date(item.get("deadline")),
            region=region or None,
            category="/".join(part for part in parts if part) or None,
            amount=parse_amount(item.get("budget"), title),
            url=url,
            summary=summary or None,
            raw=item,
        )

    @staticmethod
    def _dedupe(items: list[Tender]) -> list[Tender]:
        unique = {item.id: item for item in items}
        return sorted(unique.values(), key=lambda item: item.publish_date or "", reverse=True)

    @staticmethod
    def _resolve_classes(classify: str) -> list[str]:
        if classify in (None, "", "all", "全部"):
            return [CLASSIFY["engineering"], CLASSIFY["procurement"]]
        return [CLASSIFY.get(classify, classify)]

    @staticmethod
    def _deal_time(days: int) -> str:
        if days <= 1:
            return "01"
        if days <= 3:
            return "02"
        if days <= 10:
            return "03"
        if days <= 31:
            return "04"
        return "05"
