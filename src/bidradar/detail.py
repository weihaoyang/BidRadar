from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .models import Tender
from .textutil import clean_text, html_to_text, parse_amount, parse_date

ATTACHMENT_EXT = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|txt|ofd)(?:\?|$)", re.I
)
ATTACHMENT_HINT = re.compile(r"附件|下载|招标文件|采购文件|标书", re.I)


@dataclass
class Detail:
    content: str = ""
    buyer: str | None = None
    agency: str | None = None
    amount: float | None = None
    deadline: str | None = None
    region: str | None = None
    attachments: list[dict] = field(default_factory=list)
    html: str = ""
    method: str = "http"


class Crawl4AIClient:
    """Crawl4AI 自托管服务客户端（POST /crawl）。"""

    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = (
            base_url or os.getenv("CRAWL4AI_URL") or "http://localhost:11235"
        ).rstrip("/")
        self.token = token or os.getenv("CRAWL4AI_API_TOKEN", "")

    @property
    def configured(self) -> bool:
        return bool(os.getenv("CRAWL4AI_URL") or self.token)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def fetch_html(self, url: str, timeout: float = 120.0) -> str:
        body = {
            "urls": [url],
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {"cache_mode": "bypass"},
            },
        }
        response = httpx.post(
            f"{self.base_url}/crawl",
            json=body,
            headers=self._headers(),
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results") or []
        if not results:
            raise RuntimeError(f"Crawl4AI 没有返回结果: {data}")
        item = results[0]
        if not item.get("success", True):
            raise RuntimeError(f"Crawl4AI 抓取失败: {item.get('error_message') or item}")
        return item.get("html") or item.get("markdown") or ""


def fetch_detail(tender: Tender) -> Detail:
    """抓取并解析一条公告的详情。"""
    if tender.source == "ccgp":
        html = _fetch_http(tender.url or "")
        return _parse_ccgp(html, tender.url or "")
    if tender.source == "cebpub":
        return _fetch_cebpub(tender)
    if tender.content:
        return Detail(
            content=tender.content,
            buyer=tender.buyer,
            amount=tender.amount,
            deadline=tender.deadline,
            region=tender.region,
            attachments=tender.attachments,
            method="cached",
        )
    if not tender.url:
        return Detail()
    # 带 # 的 SPA 详情页必须走浏览器；否则 httpx 只会拿到首页。
    if "#" in tender.url:
        client = Crawl4AIClient()
        if not client.configured:
            return Detail()
        try:
            html = client.fetch_html(tender.url)
            parsed = _parse_generic(html, tender.url)
            parsed.method = "crawl4ai"
            return parsed
        except Exception:
            return Detail()
    html = _fetch_http(tender.url)
    return _parse_generic(html, tender.url)


def enrich_tender(tender: Tender) -> Tender:
    detail = fetch_detail(tender)
    if detail.content:
        tender.content = detail.content
        tender.summary = detail.content[:500]
    tender.buyer = detail.buyer or tender.buyer
    tender.agency = detail.agency or tender.agency
    tender.amount = detail.amount or tender.amount
    tender.deadline = detail.deadline or tender.deadline
    tender.region = detail.region or tender.region
    if detail.attachments:
        tender.attachments = detail.attachments
    tender.raw = dict(tender.raw or {})
    tender.raw["detail"] = {
        "method": detail.method,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return tender


def tender_from_row(row: dict) -> Tender:
    return Tender(
        source=row["source"],
        external_id=row["external_id"],
        title=row["title"],
        publish_date=row.get("publish_date"),
        deadline=row.get("deadline"),
        region=row.get("region"),
        buyer=row.get("buyer"),
        agency=row.get("agency"),
        category=row.get("category"),
        amount=row.get("amount"),
        currency=row.get("currency") or "CNY",
        url=row.get("url"),
        summary=row.get("summary"),
        content=row.get("content"),
        attachments=row.get("attachments") or [],
        raw=row.get("raw") or {},
    )


# ---- 抓取 ----
def _fetch_http(url: str, timeout: float = 40.0) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.text


def _fetch_cebpub(tender: Tender) -> Detail:
    client = Crawl4AIClient()
    if client.configured and tender.url:
        try:
            html = client.fetch_html(tender.url)
            if any(marker in html for marker in ("v-token", "验证码", "VAPTCHA", "访问过于频繁")):
                return Detail()
            parsed = _parse_generic(html, tender.url)
            parsed.method = "crawl4ai"
            if parsed.content:
                return parsed
        except Exception:
            pass
    try:
        html = _fetch_http(tender.url or "")
    except Exception:
        return Detail()
    if any(marker in html for marker in ("v-token", "验证码", "VAPTCHA", "访问过于频繁")):
        return Detail()
    parsed = _parse_generic(html, tender.url or "")
    if "v-token" in html or "验证码" in html or "暂无详细数据" in html:
        parsed.content = ""
    # 没有 Crawl4AI 时，cebpub 详情页只是壳，避免把导航文字当正文。
    if (
        not parsed.buyer
        and not parsed.amount
        and ("招标" not in parsed.content and "采购" not in parsed.content)
    ):
        parsed.content = ""
    return parsed


# ---- ccgp 详情 ----
def _parse_ccgp(html: str, base_url: str) -> Detail:
    soup = BeautifulSoup(html, "lxml")
    content_el = soup.select_one(".vF_detail_content")
    content = (
        content_el.get_text("\n", strip=True)
        if content_el
        else soup.get_text("\n", strip=True)
    )
    labels: dict[str, str] = {}
    table = soup.select_one(".table")
    if table:
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            values = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
            if len(values) >= 4:
                labels.setdefault(values[0], values[1])
                labels.setdefault(values[2], values[3])
            elif len(values) >= 2:
                labels.setdefault(values[0], values[1])

    def pick(*names: str) -> str | None:
        for name in names:
            for key, value in labels.items():
                if key.startswith(name) and value:
                    return value
        return None

    buyer = pick("采购单位", "采购人", "招标人", "建设单位")
    agency = pick("代理机构名称", "代理机构", "采购代理机构")
    region = pick("行政区域", "所在地区")
    amount = parse_amount(pick("预算金额", "合同估算价", "最高限价"), content)
    deadline = parse_date(pick("提交文件截止时间", "投标截止时间", "递交截止时间"))
    attachments = _extract_attachments(soup, base_url)
    return Detail(
        content=content[:20000],
        buyer=buyer,
        agency=agency,
        region=region,
        amount=amount,
        deadline=deadline,
        attachments=attachments,
        html=html,
    )


# ---- 通用详情 ----
def _parse_generic(html: str, base_url: str) -> Detail:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()

    candidates = []
    for selector in (
        ".ewb-trade-con",
        ".ewb-trade-main",
        ".vF_detail_content",
        ".article-content",
        ".content",
        "article",
        "#content",
        "main",
        ".detail",
        ".detail-content",
    ):
        for element in soup.select(selector):
            text = element.get_text("\n", strip=True)
            if len(text) > 200:
                candidates.append(text)
    content = max(candidates, key=len) if candidates else soup.get_text("\n", strip=True)
    content = content[:30000]
    for marker in ("项目概况", "一、项目基本情况", "招标公告", "采购公告"):
        index = content.find(marker)
        if 0 < index < 1200:
            content = content[index:]
            break

    def search(*patterns: str) -> str | None:
        for pattern in patterns:
            match = re.search(pattern + r"\s*[:：]\s*([^\n]{2,80})", content)
            if match:
                return clean_text(match.group(1))
        return None

    lines = [clean_text(line) for line in content.splitlines()]

    def line_value(*names: str) -> str | None:
        for index, line in enumerate(lines):
            bare = line.rstrip("：:").strip()
            if bare in names:
                for following in lines[index + 1 : index + 4]:
                    if following and not following.endswith(("：", ":")) and len(following) <= 80:
                        return following
        return None

    buyer = search(r"采购人", r"采购单位", r"招标人", r"建设单位") or line_value(
        "采购人", "采购单位", "招标人", "建设单位"
    )
    agency = search(r"代理机构", r"招标代理机构", r"采购代理机构") or line_value(
        "代理机构", "代理机构名称", "招标代理机构"
    )
    region = search(r"行政区域", r"项目所在地", r"建设地点") or line_value(
        "行政区域", "项目所在地", "建设地点", "所在地区"
    )
    amount_text = search(r"预算金额", r"合同估算价", r"最高限价", r"招标控制价") or line_value(
        "预算金额", "合同估算价", "最高限价", "招标控制价"
    )
    amount = parse_amount(amount_text, content)
    deadline = parse_date(
        search(r"投标截止时间", r"递交截止时间", r"提交文件截止时间", r"开标时间")
        or line_value("投标截止时间", "递交截止时间", "提交文件截止时间", "开标时间")
    )
    attachments = _extract_attachments(soup, base_url)
    return Detail(
        content=content,
        buyer=buyer,
        agency=agency,
        region=region,
        amount=amount,
        deadline=deadline,
        attachments=attachments,
        html=html,
    )


def _extract_attachments(soup: BeautifulSoup, base_url: str) -> list[dict]:
    attachments: list[dict] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = link.get("href") or ""
        text = clean_text(link.get_text(" ", strip=True))
        if not href or href.startswith(("javascript:", "#")):
            continue
        if not (ATTACHMENT_EXT.search(href) or ATTACHMENT_HINT.search(text)):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        attachments.append({"name": text or url.rsplit("/", 1)[-1], "url": url})
    return attachments[:50]
