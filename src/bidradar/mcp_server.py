from __future__ import annotations

import json

try:  # MCP SDK 1.x
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:  # MCP SDK 2.x: FastMCP renamed to MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP

from .config import load_config
from .engine import SOURCE_LABELS, Engine

mcp = FastMCP(
    "bidradar",
    instructions=(
        "招投标数据检索与监控。中国数据源：cebpub、ggzy、ccgp；"
        "海外数据源：ted（欧盟）、sam（美国，需 SAM_API_KEY）。"
        "金额单位为元或对应币种；日期为 YYYY-MM-DD。"
    ),
)


def _engine() -> Engine:
    return Engine(load_config())


@mcp.tool()
def search_tenders(
    keyword: str,
    days: int = 7,
    sources: str = "",
    limit: int = 20,
) -> str:
    """实时搜索招投标公告。

    Args:
        keyword: 关键词，例如 "信息化"、"人工智能 医疗"。
        days: 最近 N 天，默认 7。
        sources: 逗号分隔的数据源，例如 "cebpub,ggzy,ted"；留空使用配置。
        limit: 最多返回条数，默认 20。
    """
    engine = _engine()
    try:
        names = [item.strip() for item in sources.split(",") if item.strip()] or None
        tenders, warnings = engine.search(
            keyword=keyword, days=days, sources=names, limit=limit
        )
        return json.dumps(
            {
                "count": len(tenders),
                "tenders": [item.brief() for item in tenders],
                "warnings": warnings,
            },
            ensure_ascii=False,
            indent=2,
        )
    finally:
        engine.close()


@mcp.tool()
def sync_tenders(days: int = 1, sources: str = "") -> str:
    """把配置中的关键词增量抓取到本地库（用于每日增量/监控前刷新）。"""
    engine = _engine()
    try:
        names = [item.strip() for item in sources.split(",") if item.strip()] or None
        result = engine.sync(days=days, sources=names)
        return json.dumps(result, ensure_ascii=False, indent=2)
    finally:
        engine.close()


@mcp.tool()
def get_tender(tender_id: str, fetch_detail: bool = False) -> str:
    """按 ID（source:external_id）读取一条公告的完整字段。

    Args:
        tender_id: 公告 ID，例如 "cebpub:xxxx"。
        fetch_detail: 是否实时抓取详情页，补齐采购单位/预算/正文/附件。
    """
    engine = _engine()
    try:
        item = engine.store.get(tender_id)
        if not item:
            return "未找到该公告"
        if fetch_detail:
            from .detail import enrich_tender, tender_from_row

            tender = enrich_tender(tender_from_row(item))
            engine.store.upsert([tender])
            item = engine.store.get(tender_id) or item
        return json.dumps(item, ensure_ascii=False, indent=2)
    finally:
        engine.close()


@mcp.tool()
def enrich_tenders(
    source: str = "",
    limit: int = 10,
    delay: float = 1.0,
) -> str:
    """批量抓取详情，补齐采购单位/预算/正文/附件。"""
    import time

    from .detail import enrich_tender, tender_from_row

    engine = _engine()
    try:
        rows = engine.store.search(source=source or None, limit=limit * 3)
        rows = [
            row
            for row in rows
            if not row.get("content") or not row.get("buyer") or not row.get("amount")
        ][:limit]
        done = failed = 0
        errors = []
        for row in rows:
            try:
                engine.store.upsert([enrich_tender(tender_from_row(row))])
                done += 1
            except Exception as exc:
                failed += 1
                errors.append({"id": row["id"], "error": str(exc)})
            if delay:
                time.sleep(delay)
        return json.dumps(
            {"done": done, "failed": failed, "errors": errors},
            ensure_ascii=False,
            indent=2,
        )
    finally:
        engine.close()


@mcp.tool()
def list_sources() -> str:
    """列出可用数据源和启用状态。"""
    engine = _engine()
    try:
        enabled = set(engine.enabled_sources())
        data = [
            {"id": name, "name": label, "enabled": name in enabled}
            for name, label in SOURCE_LABELS.items()
        ]
        return json.dumps(data, ensure_ascii=False, indent=2)
    finally:
        engine.close()


@mcp.tool()
def export_tenders(
    fmt: str = "csv",
    output: str = "",
    keyword: str = "",
    region: str = "",
    source: str = "",
    since: str = "",
    until: str = "",
    min_amount: float = 0,
) -> str:
    """把本地库导出为 csv/json/jsonl/xlsx，返回文件路径。"""
    engine = _engine()
    try:
        path = output or engine.export_default_path(fmt)
        target = engine.store.export(
            path,
            fmt=fmt,
            keyword=keyword or None,
            region=region or None,
            source=source or None,
            since=since or None,
            until=until or None,
            min_amount=min_amount or None,
        )
        return str(target.resolve())
    finally:
        engine.close()


@mcp.tool()
def add_watch(
    name: str,
    keywords: str,
    regions: str = "",
    sources: str = "",
    exclude: str = "",
    min_amount: float = 0,
) -> str:
    """新增/更新关键词监控规则（关键词、地区、来源、排除词用逗号分隔）。"""
    engine = _engine()
    try:
        def split(value: str) -> list[str]:
            return [item.strip() for item in value.split(",") if item.strip()]

        watch_id = engine.store.add_watch(
            name=name,
            keywords=split(keywords),
            regions=split(regions),
            sources=split(sources),
            exclude=split(exclude),
            min_amount=min_amount,
        )
        return json.dumps({"id": watch_id, "name": name}, ensure_ascii=False)
    finally:
        engine.close()


@mcp.tool()
def list_watches() -> str:
    """列出全部监控规则。"""
    engine = _engine()
    try:
        return json.dumps(engine.store.list_watches(), ensure_ascii=False, indent=2)
    finally:
        engine.close()


@mcp.tool()
def run_watches(send: bool = False) -> str:
    """执行监控规则；send=true 时按配置推送 Webhook。"""
    engine = _engine()
    try:
        return json.dumps(engine.run_watches(send=send), ensure_ascii=False, indent=2)
    finally:
        engine.close()


@mcp.tool()
def get_stats() -> str:
    """返回本地库统计信息。"""
    engine = _engine()
    try:
        return json.dumps(engine.store.stats(), ensure_ascii=False, indent=2)
    finally:
        engine.close()


@mcp.resource("bidradar://stats")
def stats_resource() -> str:
    """本地库统计。"""
    engine = _engine()
    try:
        return json.dumps(engine.store.stats(), ensure_ascii=False, indent=2)
    finally:
        engine.close()


def main() -> None:
    import logging

    from dotenv import load_dotenv

    load_dotenv()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    mcp.run()


if __name__ == "__main__":
    main()
