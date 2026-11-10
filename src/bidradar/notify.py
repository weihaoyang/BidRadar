from __future__ import annotations

import json

import httpx

from .models import Tender


def send_webhook(url: str, title: str, text: str) -> bool:
    """支持飞书/企业微信/钉钉/通用 JSON Webhook。"""
    url = (url or "").strip()
    if not url:
        return False
    payload: dict
    if "open.feishu.cn" in url or "larksuite.com" in url:
        payload = {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
    elif "qyapi.weixin.qq.com" in url:
        payload = {"msgtype": "text", "text": {"content": f"{title}\n{text}"}}
    elif "oapi.dingtalk.com" in url:
        payload = {
            "msgtype": "text",
            "text": {"content": f"{title}\n{text}"},
        }
    elif "discord.com/api/webhooks" in url:
        payload = {"content": f"**{title}**\n{text}"}
    elif "hooks.slack.com" in url:
        payload = {"text": f"*{title}*\n{text}"}
    else:
        payload = {"title": title, "text": text}
    try:
        response = httpx.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return True
    except Exception:
        return False


def format_message(watch: dict, tenders: list[Tender]) -> tuple[str, str]:
    lines = [f"命中 {len(tenders)} 条新标讯："]
    for tender in tenders[:15]:
        amount = f" | {tender.amount:,.0f} {tender.currency}" if tender.amount else ""
        lines.append(
            f"- [{tender.publish_date or '-'}] {tender.title}"
            f" | {tender.region or '-'}{amount}"
        )
        if tender.url:
            lines.append(f"  {tender.url}")
    if len(tenders) > 15:
        lines.append(f"... 另有 {len(tenders) - 15} 条，请用 bidradar export 查看")
    return f"BidRadar 监控命中：{watch['name']}", "\n".join(lines)


def tenders_to_json(tenders: list[Tender]) -> str:
    return json.dumps([item.brief() for item in tenders], ensure_ascii=False, indent=2)
