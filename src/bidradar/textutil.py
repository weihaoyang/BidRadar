from __future__ import annotations

import html
import re
from datetime import date, datetime

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[\s\u3000]+")
_DATE_RE = re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})")
_COMPACT_DATE_RE = re.compile(r"\b(20\d{2})(\d{2})(\d{2})\b")
_AMOUNT_RE = re.compile(
    r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(亿元|万元|亿|万|元)"
)


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def html_to_text(value: object) -> str:
    """保留段落换行的 HTML 转文本。"""
    if value is None:
        return ""
    text = str(value)
    text = text.replace("<![CDATA[", "").replace("]]>", "")
    if "<" not in text:
        return text.strip()
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(text, "lxml")
        return soup.get_text("\n", strip=True)
    except Exception:
        return clean_text(text)


def parse_date(value: object) -> str | None:
    """把常见日期格式统一成 YYYY-MM-DD。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    match = _DATE_RE.search(text)
    if match:
        year, month, day = (int(x) for x in match.groups())
        if year < 1990:
            return None
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    match = _COMPACT_DATE_RE.search(text)
    if match:
        year, month, day = (int(x) for x in match.groups())
        if year < 1990:
            return None
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


def parse_amount(value: object, default_text: str = "") -> float | None:
    """解析金额并换算为元。"""
    candidates: list[str] = []
    if value is not None:
        candidates.append(str(value))
    if default_text:
        candidates.append(default_text)
    for text in candidates:
        match = _AMOUNT_RE.search(text.replace(",", ""))
        if not match:
            continue
        number = float(match.group(1))
        unit = match.group(2)
        multiplier = {"亿元": 100_000_000, "亿": 100_000_000, "万元": 10_000, "万": 10_000, "元": 1}[unit]
        return number * multiplier
    return None


def contains_any(text: str, words: list[str] | tuple[str, ...] | None) -> bool:
    if not words:
        return False
    return any(word and word in text for word in words)


def flatten(value: object) -> str:
    """把 TED 这类多语言字段压平成文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("eng", "ENG", "en", "fra", "deu"):
            if key in value:
                return flatten(value[key])
        for item in value.values():
            result = flatten(item)
            if result:
                return result
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(flatten(item) for item in value if flatten(item))
    return str(value)
