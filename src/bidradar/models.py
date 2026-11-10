from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Tender:
    """统一后的招投标记录。"""

    source: str
    external_id: str
    title: str
    publish_date: str | None = None
    deadline: str | None = None
    region: str | None = None
    buyer: str | None = None
    agency: str | None = None
    category: str | None = None
    amount: float | None = None
    currency: str = "CNY"
    url: str | None = None
    summary: str | None = None
    content: str | None = None
    attachments: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def id(self) -> str:
        return f"{self.source}:{self.external_id}"

    @property
    def content_hash(self) -> str:
        payload = "|".join(
            [
                self.source,
                self.external_id,
                self.title or "",
                self.publish_date or "",
                self.deadline or "",
                self.region or "",
                self.buyer or "",
                str(self.amount or ""),
                (self.content or "")[:1000],
            ]
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "external_id": self.external_id,
            "title": self.title,
            "publish_date": self.publish_date,
            "deadline": self.deadline,
            "region": self.region,
            "buyer": self.buyer,
            "agency": self.agency,
            "category": self.category,
            "amount": self.amount,
            "currency": self.currency,
            "url": self.url,
            "summary": self.summary,
            "content": self.content,
            "attachments": json.dumps(self.attachments, ensure_ascii=False, default=str),
            "raw": json.dumps(self.raw, ensure_ascii=False, default=str),
            "fetched_at": self.fetched_at,
            "content_hash": self.content_hash,
        }

    def brief(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "publish_date": self.publish_date,
            "deadline": self.deadline,
            "region": self.region,
            "buyer": self.buyer,
            "amount": self.amount,
            "currency": self.currency,
            "category": self.category,
            "url": self.url,
            "attachments": self.attachments,
        }
