from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import db_path, load_config
from .db import Store
from .models import Tender
from .sources.ccgp import CcgpSource
from .sources.cebpub import CebpubSource
from .sources.ggzy import GgzySource
from .sources.sam import SamSource
from .sources.searxng import SearxngSource
from .sources.ted import TedSource
from .sources.yaml_site import YamlSiteSource, load_site_configs
from .textutil import contains_any

SOURCE_REGISTRY = {
    "cebpub": CebpubSource,
    "ggzy": GgzySource,
    "ccgp": CcgpSource,
    "ted": TedSource,
    "sam": SamSource,
    "searxng": SearxngSource,
}

SOURCE_LABELS = {name: cls.label for name, cls in SOURCE_REGISTRY.items()}


def _sites_dir(config: dict[str, Any]) -> Path:
    return Path(config.get("_base_dir") or Path.cwd()) / "sites"


class Engine:
    def __init__(self, config: dict[str, Any] | None = None, store: Store | None = None):
        self.config = config or load_config()
        self.store = store or Store(db_path(self.config))
        self.yaml_configs = load_site_configs(_sites_dir(self.config))
        self._sources: dict[str, Any] = {}

    # ---- 数据源 ----
    def source_labels(self) -> dict[str, str]:
        labels = dict(SOURCE_LABELS)
        for name, site in self.yaml_configs.items():
            labels[name] = str(site.get("name") or name)
        return labels

    def enabled_sources(self) -> list[str]:
        configured = self.config.get("sources") or {}
        names = list(SOURCE_REGISTRY) + list(self.yaml_configs)
        defaults = {"cebpub", "ggzy", "ted", "gd", "js", "zj"}
        return [name for name in names if configured.get(name, name in defaults)]

    def get_source(self, name: str):
        if name in self.yaml_configs:
            key = f"yaml:{name}"
            if key not in self._sources:
                self._sources[key] = YamlSiteSource(self.yaml_configs[name])
            return self._sources[key]
        if name not in SOURCE_REGISTRY:
            raise KeyError(f"未知数据源: {name}")
        if name not in self._sources:
            self._sources[name] = SOURCE_REGISTRY[name]()
        return self._sources[name]

    def close(self) -> None:
        for source in self._sources.values():
            try:
                source.close()
            except Exception:
                pass
        self._sources.clear()

    # ---- 搜索 / 同步 ----
    def search(
        self,
        keyword: str,
        days: int = 7,
        sources: list[str] | None = None,
        limit: int = 50,
        apply_config_filters: bool = True,
    ) -> tuple[list[Tender], list[dict]]:
        names = sources or self.enabled_sources()
        per_source = max(10, limit // max(len(names), 1) + 5)
        results: list[Tender] = []
        warnings: list[dict] = []
        source_objects = [(name, self.get_source(name)) for name in names]
        with ThreadPoolExecutor(max_workers=min(8, max(len(source_objects), 1))) as pool:
            futures = {
                pool.submit(source.search, keyword=keyword, days=days, limit=per_source): name
                for name, source in source_objects
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results.extend(future.result())
                except Exception as exc:  # 单个源失败不影响其它源
                    warnings.append({"source": name, "error": str(exc)})

        if apply_config_filters:
            exclude = (self.config.get("filters") or {}).get("exclude") or []
            if exclude:
                results = [item for item in results if not contains_any(item.title, exclude)]

        unique = {item.id: item for item in results}
        ordered = sorted(unique.values(), key=lambda item: item.publish_date or "", reverse=True)
        return ordered[:limit], warnings

    def sync(
        self,
        days: int = 1,
        sources: list[str] | None = None,
        keywords: list[str] | None = None,
    ) -> dict:
        words = keywords or self.config.get("keywords") or []
        if isinstance(words, str):
            words = [words]
        total_new = total_updated = 0
        warnings: list[dict] = []
        for keyword in words:
            tenders, errors = self.search(
                keyword=keyword, days=days, sources=sources, limit=100, apply_config_filters=True
            )
            inserted, updated = self.store.upsert(tenders)
            total_new += inserted
            total_updated += updated
            warnings.extend(errors)
        return {
            "keywords": words,
            "days": days,
            "sources": sources or self.enabled_sources(),
            "new": total_new,
            "updated": total_updated,
            "warnings": warnings,
            "stats": self.store.stats(),
        }

    # ---- 历史回填 ----
    def backfill(
        self,
        start: str,
        end: str,
        keywords: list[str] | None = None,
        classify: str = "all",
        max_pages: int = 50,
        on_progress=None,
    ) -> dict:
        """按月分片抓取历史数据（目前支持 ggzy）。

        ggzy 单次查询总量上限约 1000 条，因此按月切片可以拿到更完整的历史数据。
        """
        if "ggzy" not in SOURCE_REGISTRY:
            raise RuntimeError("历史回填目前只支持 ggzy 数据源")
        words = keywords or self.config.get("keywords") or []
        if isinstance(words, str):
            words = [words]
        source = self.get_source("ggzy")
        total_new = total_updated = 0
        details: list[dict] = []
        warnings: list[dict] = []

        for range_start, range_end in month_ranges(start, end):
            for keyword in words:
                try:
                    tenders = source.search_range(
                        start=range_start,
                        end=range_end,
                        keyword=keyword,
                        classify=classify,
                        max_pages=max_pages,
                    )
                    inserted, updated = self.store.upsert(tenders)
                    total_new += inserted
                    total_updated += updated
                    detail = {
                        "keyword": keyword,
                        "start": range_start,
                        "end": range_end,
                        "fetched": len(tenders),
                        "new": inserted,
                        "updated": updated,
                    }
                    details.append(detail)
                    if on_progress:
                        on_progress(detail)
                except Exception as exc:
                    warnings.append(
                        {
                            "keyword": keyword,
                            "start": range_start,
                            "end": range_end,
                            "error": str(exc),
                        }
                    )
        return {
            "new": total_new,
            "updated": total_updated,
            "details": details,
            "warnings": warnings,
            "stats": self.store.stats(),
        }

    # ---- 监控 ----
    def run_watches(self, send: bool = True) -> list[dict]:
        from .notify import format_message, send_webhook

        output: list[dict] = []
        webhook = (self.config.get("notify") or {}).get("webhook") or ""
        for watch in self.store.list_watches():
            matches: dict[str, Tender] = {}
            warnings: list[dict] = []
            source_names = watch.get("sources") or None
            if isinstance(source_names, str):
                source_names = [source_names]
            for keyword in watch.get("keywords") or []:
                tenders, errors = self.search(
                    keyword=keyword,
                    days=1,
                    sources=source_names,
                    limit=100,
                    apply_config_filters=False,
                )
                warnings.extend(errors)
                global_exclude = (self.config.get("filters") or {}).get("exclude") or []
                exclude = list(watch.get("exclude") or []) + list(global_exclude)
                for tender in tenders:
                    text = f"{tender.title} {tender.region or ''} {tender.summary or ''}"
                    if contains_any(tender.title, exclude):
                        continue
                    regions = watch.get("regions") or []
                    if regions and not contains_any(text, regions):
                        continue
                    if watch.get("min_amount") and (tender.amount or 0) < watch["min_amount"]:
                        continue
                    matches[tender.id] = tender

            fresh = [
                tender
                for tender in matches.values()
                if not self.store.already_alerted(watch["id"], tender.id)
            ]
            self.store.upsert(fresh)
            sent = False
            if send and fresh and webhook:
                title, text = format_message(watch, fresh)
                sent = send_webhook(webhook, title, text)
                if sent:
                    for tender in fresh:
                        self.store.mark_alerted(watch["id"], tender.id)
            output.append(
                {
                    "watch": watch["name"],
                    "matched": len(matches),
                    "new": len(fresh),
                    "sent": sent,
                    "warnings": warnings,
                    "tenders": [item.brief() for item in fresh[:20]],
                }
            )
        return output

    # ---- 便捷方法 ----
    def export_default_path(self, fmt: str = "csv") -> str:
        stamp = date.today().strftime("%Y%m%d")
        return f"./exports/tenders_{stamp}.{ 'xlsx' if fmt in ('xlsx', 'excel') else fmt }"


def sleep_with_notice(seconds: int) -> None:
    """可中断的 sleep，方便 Ctrl+C。"""
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(min(2, end - time.time()))


def month_ranges(start: str, end: str) -> list[tuple[str, str]]:
    """把日期区间切成自然月，避免单次查询触发平台上限。"""
    import calendar

    current = date.fromisoformat(start)
    last = date.fromisoformat(end)
    ranges: list[tuple[str, str]] = []
    while current <= last:
        month_end = date(
            current.year,
            current.month,
            calendar.monthrange(current.year, current.month)[1],
        )
        ranges.append((current.isoformat(), min(month_end, last).isoformat()))
        current = month_end + timedelta(days=1)
    return ranges
