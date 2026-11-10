from __future__ import annotations

import csv
import json
import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from .models import Tender

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenders (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    publish_date TEXT,
    deadline TEXT,
    region TEXT,
    buyer TEXT,
    agency TEXT,
    category TEXT,
    amount REAL,
    currency TEXT DEFAULT 'CNY',
    url TEXT,
    summary TEXT,
    content TEXT,
    attachments TEXT,
    raw TEXT,
    fetched_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_tenders_publish ON tenders(publish_date DESC);
CREATE INDEX IF NOT EXISTS idx_tenders_source ON tenders(source);
CREATE INDEX IF NOT EXISTS idx_tenders_region ON tenders(region);

CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    keywords TEXT NOT NULL,
    regions TEXT DEFAULT '[]',
    sources TEXT DEFAULT '[]',
    exclude TEXT DEFAULT '[]',
    min_amount REAL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER,
    tender_id TEXT,
    sent_at TEXT NOT NULL,
    UNIQUE(watch_id, tender_id)
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(tenders)").fetchall()
        }
        for name in ("content", "attachments"):
            if name not in columns:
                self.conn.execute(f"ALTER TABLE tenders ADD COLUMN {name} TEXT")

    def close(self) -> None:
        self.conn.close()

    def upsert(self, tenders: Iterable[Tender]) -> tuple[int, int]:
        """返回 (新增数, 更新数)。"""
        inserted = updated = 0
        with self._lock:
            for tender in tenders:
                row = tender.to_row()
                existing = self.conn.execute(
                    "SELECT content_hash FROM tenders WHERE id = ?", (row["id"],)
                ).fetchone()
                if existing is None:
                    self.conn.execute(
                        """INSERT INTO tenders
                        (id, source, external_id, title, publish_date, deadline, region,
                         buyer, agency, category, amount, currency, url, summary, content,
                         attachments, raw, fetched_at, content_hash)
                        VALUES (:id, :source, :external_id, :title, :publish_date, :deadline,
                                :region, :buyer, :agency, :category, :amount, :currency,
                                :url, :summary, :content, :attachments, :raw, :fetched_at,
                                :content_hash)""",
                        row,
                    )
                    inserted += 1
                elif existing["content_hash"] != row["content_hash"]:
                    self.conn.execute(
                        """UPDATE tenders SET title=:title, publish_date=:publish_date,
                        deadline=:deadline, region=:region, buyer=:buyer, agency=:agency,
                        category=:category, amount=:amount, currency=:currency, url=:url,
                        summary=:summary, content=:content, attachments=:attachments,
                        raw=:raw, fetched_at=:fetched_at,
                        content_hash=:content_hash WHERE id=:id""",
                        row,
                    )
                    updated += 1
            self.conn.commit()
        return inserted, updated

    def search(
        self,
        keyword: str | None = None,
        region: str | None = None,
        source: str | None = None,
        since: str | None = None,
        until: str | None = None,
        min_amount: float | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        where: list[str] = []
        params: list = []
        if keyword:
            words = [word for word in keyword.split() if word]
            if words:
                clauses = []
                for word in words:
                    clauses.append(
                        "(title LIKE ? OR buyer LIKE ? OR summary LIKE ? OR region LIKE ? OR content LIKE ?)"
                    )
                    like = f"%{word}%"
                    params.extend([like, like, like, like, like])
                where.append("(" + " OR ".join(clauses) + ")")
        if region:
            where.append("region LIKE ?")
            params.append(f"%{region}%")
        if source:
            sources = [item.strip() for item in source.split(",") if item.strip()]
            if sources:
                where.append("source IN (" + ",".join("?" for _ in sources) + ")")
                params.extend(sources)
        if since:
            where.append("publish_date >= ?")
            params.append(since)
        if until:
            where.append("publish_date <= ?")
            params.append(until)
        if min_amount:
            where.append("amount >= ?")
            params.append(min_amount)
        sql = "SELECT * FROM tenders"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY COALESCE(publish_date, '') DESC, fetched_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get(self, tender_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM tenders WHERE id = ?", (tender_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) AS n FROM tenders").fetchone()["n"]
        by_source = [
            dict(row)
            for row in self.conn.execute(
                "SELECT source, COUNT(*) AS count FROM tenders GROUP BY source ORDER BY count DESC"
            ).fetchall()
        ]
        latest = self.conn.execute(
            "SELECT MAX(publish_date) AS latest, MAX(fetched_at) AS fetched FROM tenders"
        ).fetchone()
        return {
            "total": total,
            "by_source": by_source,
            "latest_publish_date": latest["latest"] if latest else None,
            "last_fetch_at": latest["fetched"] if latest else None,
            "db": str(self.path),
        }

    def export(
        self,
        path: str | Path,
        fmt: str = "csv",
        **filters,
    ) -> Path:
        rows = self.search(limit=1_000_000, **filters)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fmt = fmt.lower()
        if fmt in ("json", "jsonl"):
            if fmt == "jsonl":
                with target.open("w", encoding="utf-8") as handle:
                    for row in rows:
                        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            else:
                target.write_text(
                    json.dumps(rows, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
        elif fmt == "csv":
            fields = [
                "id", "source", "title", "publish_date", "deadline", "region",
                "buyer", "agency", "category", "amount", "currency", "url",
                "summary", "content", "attachments",
            ]
            with target.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(self._flatten_export_row(row) for row in rows)
        elif fmt in ("xlsx", "excel"):
            try:
                from openpyxl import Workbook
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("导出 xlsx 需要安装 openpyxl: pip install openpyxl") from exc
            workbook = Workbook(write_only=True)
            sheet = workbook.create_sheet("tenders")
            fields = [
                "id", "source", "title", "publish_date", "deadline", "region",
                "buyer", "agency", "category", "amount", "currency", "url",
                "summary", "content", "attachments",
            ]
            sheet.append(fields)
            for row in rows:
                flat = self._flatten_export_row(row)
                sheet.append([flat.get(field) for field in fields])
            workbook.save(target)
        else:
            raise ValueError(f"不支持的导出格式: {fmt}")
        return target

    # ---- 监控规则 ----
    def add_watch(
        self,
        name: str,
        keywords: list[str],
        regions: list[str] | None = None,
        sources: list[str] | None = None,
        exclude: list[str] | None = None,
        min_amount: float = 0,
    ) -> int:
        from datetime import datetime, timezone

        with self._lock:
            cursor = self.conn.execute(
                """INSERT INTO watches (name, keywords, regions, sources, exclude, min_amount, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET keywords=excluded.keywords,
                regions=excluded.regions, sources=excluded.sources,
                exclude=excluded.exclude, min_amount=excluded.min_amount""",
                (
                    name,
                    json.dumps(keywords, ensure_ascii=False),
                    json.dumps(regions or [], ensure_ascii=False),
                    json.dumps(sources or [], ensure_ascii=False),
                    json.dumps(exclude or [], ensure_ascii=False),
                    min_amount,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            self.conn.commit()
            return int(cursor.lastrowid or 0)

    def list_watches(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM watches ORDER BY id").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("keywords", "regions", "sources", "exclude"):
                try:
                    item[key] = json.loads(item.get(key) or "[]")
                except json.JSONDecodeError:
                    item[key] = []
            result.append(item)
        return result

    def remove_watch(self, name: str) -> bool:
        with self._lock:
            cursor = self.conn.execute("DELETE FROM watches WHERE name = ?", (name,))
            self.conn.commit()
            return cursor.rowcount > 0

    def already_alerted(self, watch_id: int, tender_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM alerts WHERE watch_id = ? AND tender_id = ?",
            (watch_id, tender_id),
        ).fetchone()
        return row is not None

    def mark_alerted(self, watch_id: int, tender_id: str) -> None:
        from datetime import datetime, timezone

        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO alerts (watch_id, tender_id, sent_at) VALUES (?, ?, ?)",
                (watch_id, tender_id, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            self.conn.commit()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        item = dict(row)
        raw = item.pop("raw", None)
        item["raw"] = json.loads(raw) if raw else {}
        attachments = item.pop("attachments", None)
        item["attachments"] = json.loads(attachments) if attachments else []
        return item

    @staticmethod
    def _flatten_export_row(row: dict) -> dict:
        item = dict(row)
        attachments = item.get("attachments") or []
        item["attachments"] = " | ".join(
            entry.get("url") or entry.get("name") or ""
            for entry in attachments
            if isinstance(entry, dict)
        )
        return item
