import json
import sqlite3
from pathlib import Path
from typing import Iterable

from models import Item
from utils import fingerprint

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    event_time TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    url TEXT,
    payload_json TEXT,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_items_time ON items(event_time DESC);
CREATE INDEX IF NOT EXISTS idx_items_code_category ON items(code, category);
"""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(items)").fetchall()
    }
    if "subcategory" not in columns:
        conn.execute(
            "ALTER TABLE items ADD COLUMN subcategory TEXT NOT NULL DEFAULT ''"
        )
    return conn


def upsert_items(db_path: str, items: Iterable[Item]) -> tuple[int, int]:
    inserted = 0
    ignored = 0
    with connect(db_path) as conn:
        for item in items:
            key = fingerprint(item.code, item.category, item.source, item.event_time, item.title, item.url)
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO items
                (dedupe_key, code, name, category, subcategory, source,
                 event_time, title, summary, url, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    item.code,
                    item.name,
                    item.category,
                    item.subcategory,
                    item.source,
                    item.event_time,
                    item.title,
                    item.summary,
                    item.url,
                    json.dumps(item.payload, ensure_ascii=False, default=str),
                ),
            )
            if cur.rowcount:
                inserted += 1
            else:
                ignored += 1
    return inserted, ignored


def fetch_recent(db_path: str, since_iso: str, codes=None, categories=None):
    sql = "SELECT * FROM items WHERE event_time >= ?"
    params = [since_iso]
    if codes is not None:
        if not codes:
            return []
        sql += " AND code IN (%s)" % ",".join("?" for _ in codes)
        params.extend(codes)
    if categories:
        sql += " AND category IN (%s)" % ",".join("?" for _ in categories)
        params.extend(categories)
    sql += " ORDER BY event_time DESC"
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
