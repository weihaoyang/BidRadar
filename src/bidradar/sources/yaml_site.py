from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml
from bs4 import BeautifulSoup

from ..models import Tender
from ..textutil import clean_text, html_to_text, parse_amount, parse_date
from .base import BaseSource, SourceError

_PLACEHOLDER = re.compile(r"\$\{([a-zA-Z0-9_]+)\}")
DEFAULT_DATE_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S", "%Y-%m-%d"]


def load_site_configs(directory: str | Path | None = None) -> dict[str, dict]:
    base = Path(directory) if directory else Path.cwd() / "sites"
    if not base.exists():
        return {}
    configs: dict[str, dict] = {}
    for path in sorted(base.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict) or not data.get("id"):
            continue
        if data.get("enabled", True) is False:
            continue
        data["_path"] = str(path)
        configs[data["id"]] = data
    return configs


def get_path(obj: Any, path: str | None) -> Any:
    if not path:
        return None
    current = obj
    for part in str(path).split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def render_template(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _PLACEHOLDER.sub(
            lambda match: str(context.get(match.group(1), match.group(0))), value
        )
    if isinstance(value, dict):
        return {key: render_template(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render_template(item, context) for item in value]
    return value


def parse_site_date(value: Any, formats: list[str] | None = None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in formats or DEFAULT_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return parse_date(text)


class YamlSiteSource(BaseSource):
    """由 sites/*.yaml 驱动的省市级交易中心连接器。

    支持两种模式：
    - json: 请求 JSON 接口，按 dot-path 取列表和字段
    - html: 请求 HTML 列表页，按 CSS 选择器取字段（可选 Crawl4AI 渲染）
    """

    def __init__(self, config: dict[str, Any], timeout: float = 30.0):
        self.config = config
        self.id = str(config["id"])
        self.label = str(config.get("name") or self.id)
        self.referer = (
            config.get("referer")
            or (config.get("headers") or {}).get("Referer")
            or None
        )
        self.min_interval = float(config.get("min_interval", 1.0))
        super().__init__(timeout=timeout)

    def search(self, keyword: str, days: int = 7, limit: int = 50) -> list[Tender]:
        mode = str(self.config.get("type", "json")).lower()
        if mode == "html":
            return self._search_html(keyword=keyword, days=days, limit=limit)
        return self._search_json(keyword=keyword, days=days, limit=limit)

    # ---- JSON 模式 ----
    def _search_json(self, keyword: str, days: int, limit: int) -> list[Tender]:
        page_size = int(self.config.get("page_size", 20))
        page_start = int(self.config.get("page_start", 1))
        max_pages = int(self.config.get("max_pages", 10))
        date_formats = self.config.get("date_formats") or DEFAULT_DATE_FORMATS
        start, end = self._date_range(days)
        results: list[Tender] = []
        total_pages = 1

        for offset in range(max_pages):
            page = page_start + offset
            context = self._context(keyword, start, end, page, page_size, offset)
            body = render_template(self.config.get("body") or {}, context)
            params = render_template(self.config.get("params") or {}, context)
            headers = dict(self.config.get("headers") or {})
            method = str(self.config.get("method", "POST")).upper()
            url = render_template(self.config["url"], context)
            if method == "GET":
                response = self.get(url, params=params or body or None, headers=headers)
            else:
                response = self.post(url, json=body, params=params or None, headers=headers)
            try:
                data = response.json()
            except ValueError as exc:
                raise SourceError(f"{self.id} 返回了非 JSON 内容") from exc

            items = get_path(data, self.config.get("list_path"))
            if not isinstance(items, list):
                items = []
            total = get_path(data, self.config.get("total_path"))
            try:
                total = int(total or 0)
            except (TypeError, ValueError):
                total = 0
            if total:
                total_pages = max(1, (total + page_size - 1) // page_size)

            for item in items:
                tender = self._map_item(item, date_formats)
                if tender is None:
                    continue
                cutoff = start.isoformat()
                if tender.publish_date and tender.publish_date < cutoff:
                    continue
                results.append(tender)
                if len(results) >= limit:
                    return self._dedupe(results)[:limit]

            if not items or page >= total_pages:
                break
        return self._dedupe(results)[:limit]

    # ---- HTML 模式 ----
    def _search_html(self, keyword: str, days: int, limit: int) -> list[Tender]:
        page_start = int(self.config.get("page_start", 1))
        max_pages = int(self.config.get("max_pages", 10))
        start, end = self._date_range(days)
        results: list[Tender] = []
        selectors = self.config.get("selectors") or {}
        item_selector = selectors.get("item")
        if not item_selector:
            raise SourceError(f"{self.id} 的 YAML 缺少 selectors.item")

        for offset in range(max_pages):
            page = page_start + offset
            context = self._context(keyword, start, end, page, 20, offset)
            url = render_template(self.config["url"], context)
            html = self._fetch_html(url)
            soup = BeautifulSoup(html, "lxml")
            items = soup.select(item_selector)
            if not items:
                break
            for item in items:
                title = clean_text(
                    item.select_one(selectors["title"]).get_text(" ", strip=True)
                    if selectors.get("title") and item.select_one(selectors["title"])
                    else ""
                )
                if not title:
                    continue
                link = item.select_one(selectors.get("link", "a"))
                href = link.get("href") if link else ""
                detail_url = urljoin(url, href or "")
                publish_text = (
                    item.select_one(selectors["publish_date"]).get_text(" ", strip=True)
                    if selectors.get("publish_date") and item.select_one(selectors["publish_date"])
                    else ""
                )
                publish = parse_site_date(publish_text, self.config.get("date_formats"))
                if publish and publish < start.isoformat():
                    continue
                content = (
                    item.select_one(selectors["content"]).get_text("\n", strip=True)
                    if selectors.get("content") and item.select_one(selectors["content"])
                    else ""
                )
                results.append(
                    Tender(
                        source=self.id,
                        external_id=detail_url or title,
                        title=title,
                        publish_date=publish,
                        region=clean_text(
                            item.select_one(selectors["region"]).get_text(" ", strip=True)
                        )
                        if selectors.get("region") and item.select_one(selectors["region"])
                        else None,
                        buyer=clean_text(
                            item.select_one(selectors["buyer"]).get_text(" ", strip=True)
                        )
                        if selectors.get("buyer") and item.select_one(selectors["buyer"])
                        else None,
                        category=clean_text(
                            item.select_one(selectors["category"]).get_text(" ", strip=True)
                        )
                        if selectors.get("category") and item.select_one(selectors["category"])
                        else None,
                        amount=parse_amount(None, title),
                        url=detail_url or None,
                        summary=content[:500] or None,
                        content=content or None,
                        raw={"html": str(item)[:5000]},
                    )
                )
                if len(results) >= limit:
                    return self._dedupe(results)[:limit]
        return self._dedupe(results)[:limit]

    def _fetch_html(self, url: str) -> str:
        render = bool(self.config.get("render"))
        if render:
            from ..detail import Crawl4AIClient

            client = Crawl4AIClient()
            if client.configured:
                return client.fetch_html(url)
        response = self.get(url, headers=dict(self.config.get("headers") or {}))
        return response.text

    # ---- 字段映射 ----
    def _map_item(self, item: dict, date_formats: list[str]) -> Tender | None:
        fields = self.config.get("fields") or {}
        title = clean_text(get_path(item, fields.get("title")) or "")
        if not title:
            return None
        content_raw = get_path(item, fields.get("content"))
        content = html_to_text(content_raw) if content_raw else None
        attachments_raw = get_path(item, fields.get("attachments"))
        attachments = self._parse_attachments(attachments_raw)
        url = self._build_url(item, fields)
        amount = parse_amount(get_path(item, fields.get("amount")), title + " " + (content or ""))
        return Tender(
            source=self.id,
            external_id=str(
                get_path(item, fields.get("external_id"))
                or url
                or title
            ),
            title=title,
            publish_date=parse_site_date(
                get_path(item, fields.get("publish_date")), date_formats
            ),
            deadline=parse_site_date(
                get_path(item, fields.get("deadline")), date_formats
            ),
            region=clean_text(get_path(item, fields.get("region"))) or None,
            buyer=clean_text(get_path(item, fields.get("buyer"))) or None,
            agency=clean_text(get_path(item, fields.get("agency"))) or None,
            category=clean_text(get_path(item, fields.get("category"))) or None,
            amount=amount,
            currency=str(self.config.get("currency", "CNY")),
            url=url,
            summary=(content or "")[:500] or None,
            content=content,
            attachments=attachments,
            raw=item,
        )

    def _build_url(self, item: dict, fields: dict) -> str | None:
        template = fields.get("url_template")
        if template:
            try:
                return template.format(**item)
            except (KeyError, ValueError):
                return None
        value = get_path(item, fields.get("url"))
        return str(value) if value else None

    @staticmethod
    def _parse_attachments(value: Any) -> list[dict]:
        if not value:
            return []
        if isinstance(value, list):
            result = []
            for entry in value:
                if isinstance(entry, dict):
                    result.append(
                        {
                            "name": clean_text(entry.get("name") or entry.get("fileName")),
                            "url": entry.get("url") or entry.get("downloadUrl") or "",
                        }
                    )
                else:
                    result.append({"name": clean_text(entry), "url": ""})
            return result
        names = [part.strip() for part in re.split(r"[;,；、]", str(value)) if part.strip()]
        return [{"name": name, "url": ""} for name in names]

    def _context(
        self,
        keyword: str,
        start: date,
        end: date,
        page: int,
        page_size: int,
        offset: int,
    ) -> dict[str, Any]:
        return {
            "keyword": keyword,
            "page": page,
            "page_size": page_size,
            "pn": offset * page_size,
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "start_compact": start.strftime("%Y%m%d%H%M%S"),
            "end_compact": end.strftime("%Y%m%d%H%M%S"),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "start_yyyymmdd": start.strftime("%Y%m%d"),
            "end_yyyymmdd": end.strftime("%Y%m%d"),
        }

    @staticmethod
    def _date_range(days: int) -> tuple[date, date]:
        end = date.today()
        start = end - timedelta(days=max(days, 0))
        return start, end

    @staticmethod
    def _dedupe(items: list[Tender]) -> list[Tender]:
        unique = {item.id: item for item in items}
        return sorted(unique.values(), key=lambda item: item.publish_date or "", reverse=True)
