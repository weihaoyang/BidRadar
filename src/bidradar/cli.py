from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from .config import db_path, load_config
from .db import Store
from .engine import Engine, sleep_with_notice

app = typer.Typer(
    name="bidradar",
    help="招投标数据获取 CLI：中国 + 海外公开源，支持搜索、增量同步、导出、监控告警和 MCP。",
    no_args_is_help=True,
)
watch_app = typer.Typer(help="关键词监控规则")
app.add_typer(watch_app, name="watch")

console = Console(
    legacy_windows=False,
    width=None if sys.stdout.isatty() else 180,
)


def _engine() -> Engine:
    return Engine()


def _print_tenders(tenders: list[dict] | list, as_json: bool = False) -> None:
    if as_json:
        rows = [item.brief() if hasattr(item, "brief") else item for item in tenders]
        console.print_json(data=rows)
        return
    table = Table(box=box.ASCII, show_lines=False, header_style="bold cyan")
    table.add_column("日期", width=10)
    table.add_column("来源", width=9)
    table.add_column("标题", overflow="fold", max_width=60)
    table.add_column("地区", width=12)
    table.add_column("金额", justify="right", width=12)
    if not tenders:
        console.print("[yellow]没有结果。[/yellow]")
        return
    for item in tenders[:100]:
        row = item.brief() if hasattr(item, "brief") else item
        amount = f"{row['amount']:,.0f}" if row.get("amount") else ""
        table.add_row(
            str(row.get("publish_date") or "")[:10],
            str(row.get("source") or ""),
            str(row.get("title") or ""),
            str(row.get("region") or ""),
            amount,
        )
    console.print(table)
    if len(tenders) > 100:
        console.print(f"[dim]仅显示前 100 条，共 {len(tenders)} 条。[/dim]")


@app.command()
def init(
    force: bool = typer.Option(False, "--force", help="覆盖已有 config.yaml"),
) -> None:
    """初始化配置和数据库。"""
    from .config import write_default_config

    config_file = Path("config.yaml")
    if force and config_file.exists():
        config_file.unlink()
    target = write_default_config(config_file)
    console.print(f"[green]配置就绪[/green] {target}")
    config = load_config()
    store = Store(db_path(config))
    console.print(f"[green]数据库就绪[/green] {store.path}")
    store.close()


@app.command()
def sources() -> None:
    """列出数据源及启用状态。"""
    config = load_config()
    engine = Engine(config)
    enabled = set(engine.enabled_sources())
    table = Table(box=box.ASCII, header_style="bold cyan")
    table.add_column("ID")
    table.add_column("名称")
    table.add_column("状态")
    table.add_column("说明")
    descriptions = {
        "cebpub": "全国招标公告/公示 JSON 接口，覆盖央企+省市平台",
        "ggzy": "全国公共资源交易平台，覆盖工程+政府采购",
        "ccgp": "中国政府采购网，HTML 检索，限流较严",
        "ted": "EU TED 官方 API，无需 Key",
        "sam": "美国 SAM.gov，需要 SAM_API_KEY",
        "searxng": "可选：自建 SearXNG 全网发现，需要 SEARXNG_URL",
        "gd": "广东省公共资源交易平台（YAML JSON 接口）",
        "js": "江苏省公共资源交易平台（inteligentsearch JSON 接口）",
        "zj": "浙江省公共资源交易服务平台（inteligentsearch JSON 接口）",
    }
    for name, label in engine.source_labels().items():
        table.add_row(
            name,
            label,
            "[green]启用[/green]" if name in enabled else "[dim]关闭[/dim]",
            descriptions.get(name, ""),
        )
    console.print(table)
    engine.close()


@app.command()
def search(
    keyword: str = typer.Argument(..., help="关键词，多个关键词用空格分隔"),
    days: int = typer.Option(7, "--days", "-d", help="最近 N 天"),
    source: str = typer.Option("", "--source", "-s", help="数据源，逗号分隔；默认用配置"),
    limit: int = typer.Option(50, "--limit", "-n", help="最多返回条数"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
    save: bool = typer.Option(False, "--save", help="把结果写入本地库"),
) -> None:
    """实时搜索标讯。"""
    engine = _engine()
    names = [item.strip() for item in source.split(",") if item.strip()] or None
    tenders, warnings = engine.search(keyword=keyword, days=days, sources=names, limit=limit)
    if save and tenders:
        inserted, updated = engine.store.upsert(tenders)
        console.print(f"[dim]已写入数据库：新增 {inserted}，更新 {updated}[/dim]")
    _print_tenders(tenders, as_json=as_json)
    for warning in warnings:
        console.print(f"[yellow]{warning['source']}: {warning['error']}[/yellow]")
    engine.close()


@app.command()
def sync(
    days: int = typer.Option(1, "--days", "-d", help="最近 N 天"),
    source: str = typer.Option("", "--source", "-s", help="数据源，逗号分隔"),
    keyword: str = typer.Option("", "--keyword", "-k", help="临时关键词，逗号分隔；默认用配置"),
) -> None:
    """增量抓取配置中的关键词并写入数据库。"""
    engine = _engine()
    names = [item.strip() for item in source.split(",") if item.strip()] or None
    keywords = [item.strip() for item in keyword.split(",") if item.strip()] or None
    result = engine.sync(days=days, sources=names, keywords=keywords)
    console.print(
        f"[green]同步完成[/green] 关键词={result['keywords']} "
        f"新增={result['new']} 更新={result['updated']} 总数={result['stats']['total']}"
    )
    for warning in result["warnings"]:
        console.print(f"[yellow]{warning['source']}: {warning['error']}[/yellow]")
    engine.close()


@app.command()
def backfill(
    keyword: str = typer.Option(..., "--keyword", "-k", help="关键词，逗号分隔"),
    start: str = typer.Option(..., "--start", help="开始日期 YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="结束日期 YYYY-MM-DD"),
    classify: str = typer.Option(
        "all", "--classify", help="all/engineering/procurement/land/property..."
    ),
    max_pages: int = typer.Option(50, "--max-pages", help="每个月最多抓取页数"),
) -> None:
    """历史回填（按自然月切片，适合一次性全量导出）。"""
    engine = _engine()
    keywords = [item.strip() for item in keyword.split(",") if item.strip()]

    def progress(detail: dict) -> None:
        console.print(
            f"[dim]{detail['start']} ~ {detail['end']}[/dim] "
            f"{detail['keyword']} 抓取={detail['fetched']} "
            f"新增={detail['new']} 更新={detail['updated']}"
        )

    result = engine.backfill(
        start=start,
        end=end,
        keywords=keywords,
        classify=classify,
        max_pages=max_pages,
        on_progress=progress,
    )
    console.print(
        f"[green]回填完成[/green] 新增={result['new']} 更新={result['updated']} "
        f"总数={result['stats']['total']}"
    )
    for warning in result["warnings"]:
        console.print(
            f"[yellow]{warning['start']}~{warning['end']} "
            f"{warning['keyword']}: {warning['error']}[/yellow]"
        )
    engine.close()


@app.command()
def export(
    fmt: str = typer.Option("csv", "--format", "-f", help="csv/json/jsonl/xlsx"),
    output: str = typer.Option("", "--output", "-o", help="输出文件路径"),
    keyword: str = typer.Option("", "--keyword", "-k", help="关键词过滤"),
    region: str = typer.Option("", "--region", help="地区过滤"),
    source: str = typer.Option("", "--source", "-s", help="来源过滤"),
    since: str = typer.Option("", "--since", help="开始日期 YYYY-MM-DD"),
    until: str = typer.Option("", "--until", help="结束日期 YYYY-MM-DD"),
    min_amount: float = typer.Option(0, "--min-amount", help="最低金额（元）"),
) -> None:
    """把本地库导出为 CSV/JSON/Excel。"""
    engine = _engine()
    path = output or engine.export_default_path(fmt)
    filters = {
        "keyword": keyword or None,
        "region": region or None,
        "source": source or None,
        "since": since or None,
        "until": until or None,
        "min_amount": min_amount or None,
    }
    target = engine.store.export(path, fmt=fmt, **filters)
    console.print(f"[green]已导出[/green] {target.resolve()}")
    engine.close()


@watch_app.command("add")
def watch_add(
    name: str = typer.Argument(..., help="规则名称"),
    keyword: str = typer.Option(..., "--keyword", "-k", help="关键词，逗号分隔"),
    region: str = typer.Option("", "--region", help="地区，逗号分隔"),
    source: str = typer.Option("", "--source", "-s", help="数据源，逗号分隔"),
    exclude: str = typer.Option("", "--exclude", help="排除词，逗号分隔"),
    min_amount: float = typer.Option(0, "--min-amount", help="最低金额（元）"),
) -> None:
    """新增/更新监控规则。"""
    engine = _engine()

    def split(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    watch_id = engine.store.add_watch(
        name=name,
        keywords=split(keyword),
        regions=split(region),
        sources=split(source),
        exclude=split(exclude),
        min_amount=min_amount,
    )
    console.print(f"[green]已保存规则[/green] #{watch_id} {name}")
    engine.close()


@watch_app.command("list")
def watch_list() -> None:
    """列出监控规则。"""
    engine = _engine()
    table = Table(box=box.ASCII, header_style="bold cyan")
    table.add_column("ID", width=4)
    table.add_column("名称")
    table.add_column("关键词")
    table.add_column("地区")
    table.add_column("来源")
    table.add_column("最低金额", justify="right")
    for item in engine.store.list_watches():
        table.add_row(
            str(item["id"]),
            item["name"],
            ", ".join(item["keywords"]),
            ", ".join(item["regions"]) or "-",
            ", ".join(item["sources"]) or "全部启用",
            f"{item['min_amount']:,.0f}" if item["min_amount"] else "-",
        )
    console.print(table)
    engine.close()


@watch_app.command("remove")
def watch_remove(name: str = typer.Argument(...)) -> None:
    """删除监控规则。"""
    engine = _engine()
    if engine.store.remove_watch(name):
        console.print(f"[green]已删除[/green] {name}")
    else:
        console.print(f"[yellow]未找到[/yellow] {name}")
    engine.close()


@watch_app.command("run")
def watch_run(
    no_send: bool = typer.Option(False, "--no-send", help="只计算，不发送 Webhook"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """执行全部监控规则并推送。"""
    engine = _engine()
    results = engine.run_watches(send=not no_send)
    if as_json:
        console.print_json(data=results)
    else:
        for item in results:
            console.print(
                f"[cyan]{item['watch']}[/cyan] 命中={item['matched']} 新增={item['new']} "
                f"推送={'是' if item['sent'] else '否'}"
            )
            _print_tenders(item["tenders"])
            for warning in item["warnings"]:
                console.print(f"[yellow]{warning['source']}: {warning['error']}[/yellow]")
    engine.close()


@app.command()
def daemon(
    interval: int = typer.Option(600, "--interval", help="轮询间隔（秒）"),
    days: int = typer.Option(1, "--days", "-d", help="每次抓取最近 N 天"),
    once: bool = typer.Option(False, "--once", help="只跑一轮"),
) -> None:
    """准实时轮询：同步 + 执行监控规则。"""
    console.print(f"[green]BidRadar daemon[/green] interval={interval}s days={days}")
    while True:
        engine = _engine()
        try:
            result = engine.sync(days=days)
            watches = engine.run_watches()
            console.print(
                f"[dim]{time.strftime('%Y-%m-%d %H:%M:%S')}[/dim] "
                f"新增={result['new']} 更新={result['updated']} 规则={len(watches)}"
            )
            for warning in result["warnings"]:
                console.print(f"[yellow]{warning['source']}: {warning['error']}[/yellow]")
        except KeyboardInterrupt:
            console.print("\n[dim]已停止[/dim]")
            break
        finally:
            engine.close()
        if once:
            break
        try:
            sleep_with_notice(interval)
        except KeyboardInterrupt:
            console.print("\n[dim]已停止[/dim]")
            break


@app.command()
def stats() -> None:
    """查看本地库统计。"""
    engine = _engine()
    data = engine.store.stats()
    console.print_json(data=data)
    engine.close()


@app.command()
def detail(
    tender_id: str = typer.Argument(..., help="公告 ID，例如 cebpub:xxxx"),
    no_save: bool = typer.Option(False, "--no-save", help="只抓取，不写回数据库"),
) -> None:
    """抓取单条公告详情，补齐采购单位/预算/正文/附件。"""
    from .detail import enrich_tender, tender_from_row

    engine = _engine()
    row = engine.store.get(tender_id)
    if not row:
        console.print(f"[red]未找到公告[/red] {tender_id}")
        engine.close()
        raise typer.Exit(1)
    tender = enrich_tender(tender_from_row(row))
    if not no_save:
        engine.store.upsert([tender])
    console.print_json(data=tender.brief())
    if tender.content:
        console.print(tender.content[:1500])
    if tender.attachments:
        console.print("[bold]附件：[/bold]")
        for entry in tender.attachments:
            console.print(f"- {entry.get('name') or ''} {entry.get('url') or ''}")
    engine.close()


@app.command()
def enrich(
    source: str = typer.Option("", "--source", "-s", help="只处理指定来源"),
    limit: int = typer.Option(20, "--limit", "-n", help="最多处理条数"),
    all_records: bool = typer.Option(False, "--all", help="不跳过已有正文/采购单位的记录"),
    delay: float = typer.Option(1.0, "--delay", help="每条之间的间隔秒数"),
) -> None:
    """批量补全详情（按来源从本地库取记录）。"""
    from .detail import enrich_tender, tender_from_row

    engine = _engine()
    rows = engine.store.search(source=source or None, limit=limit * 3)
    if not all_records:
        rows = [
            row
            for row in rows
            if not row.get("content") or not row.get("buyer") or not row.get("amount")
        ]
    rows = rows[:limit]
    done = failed = 0
    for row in rows:
        try:
            tender = enrich_tender(tender_from_row(row))
            engine.store.upsert([tender])
            done += 1
            console.print(
                f"[green]OK[/green] {row['id']} "
                f"buyer={tender.buyer or '-'} amount={tender.amount or '-'} "
                f"content={len(tender.content or '')}"
            )
        except Exception as exc:
            failed += 1
            console.print(f"[yellow]FAIL[/yellow] {row['id']}: {exc}")
        if delay:
            time.sleep(delay)
    console.print(f"[green]完成[/green] 成功={done} 失败={failed}")
    engine.close()


@app.command()
def doctor() -> None:
    """逐个数据源做连通性测试。"""
    config = load_config()
    engine = Engine(config)
    table = Table(box=box.ASCII, header_style="bold cyan")
    table.add_column("数据源")
    table.add_column("状态")
    table.add_column("耗时")
    table.add_column("结果/错误")
    for name in engine.enabled_sources():
        started = time.time()
        try:
            tenders = engine.get_source(name).search(keyword="信息化", days=7, limit=3)
            elapsed = time.time() - started
            table.add_row(name, "[green]OK[/green]", f"{elapsed:.2f}s", f"{len(tenders)} 条")
        except Exception as exc:
            elapsed = time.time() - started
            table.add_row(name, "[red]FAIL[/red]", f"{elapsed:.2f}s", str(exc)[:120])
    console.print(table)
    engine.close()


@app.command()
def mcp() -> None:
    """启动 MCP Server（stdio）。"""
    from .mcp_server import main

    main()


def main() -> None:
    import logging
    import os
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if os.name == "nt":
        os.system("chcp 65001 >nul")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    app()


if __name__ == "__main__":
    main()
