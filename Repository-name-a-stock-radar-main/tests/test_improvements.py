import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from collectors.eastmoney_ir import collect_eastmoney_ir
from db import connect, fetch_recent, upsert_items
from models import Item
from stocks import Stock, add_watchlist_codes, get_watchlist, infer_market


class WatchlistTests(unittest.TestCase):
    def test_add_multiple_codes_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom.json"
            added = add_watchlist_codes(
                "600519, 000858\n920999",
                path=path,
                resolver=lambda code: f"股票{code}",
            )

            self.assertEqual([s.code for s in added], ["600519", "000858", "920999"])
            self.assertEqual([s.market for s in added], ["sh", "sz", "bj"])
            self.assertTrue(path.exists())
            combined = get_watchlist(path)
            self.assertTrue({"600519", "000858", "920999"} <= {s.code for s in combined})

            duplicate = add_watchlist_codes(
                "600519", path=path, resolver=lambda code: code
            )
            self.assertEqual(duplicate, [])

    def test_market_inference(self):
        self.assertEqual(infer_market("688001"), "sh")
        self.assertEqual(infer_market("300001"), "sz")
        self.assertEqual(infer_market("920001"), "bj")


class DatabaseMigrationTests(unittest.TestCase):
    def test_old_database_gets_subcategory_without_data_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            with sqlite3.connect(path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        dedupe_key TEXT NOT NULL UNIQUE,
                        code TEXT NOT NULL,
                        name TEXT NOT NULL,
                        category TEXT NOT NULL,
                        source TEXT NOT NULL,
                        event_time TEXT NOT NULL,
                        title TEXT NOT NULL,
                        summary TEXT,
                        url TEXT,
                        payload_json TEXT,
                        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
                    );
                    INSERT INTO items
                    (dedupe_key, code, name, category, source, event_time, title)
                    VALUES ('old', '000001', '平安银行', 'news', 'old',
                            '2026-08-13T10:00:00+08:00', '旧数据');
                    """
                )

            with connect(str(path)) as conn:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(items)")}
                count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            self.assertIn("subcategory", columns)
            self.assertEqual(count, 1)

            upsert_items(
                str(path),
                [
                    Item(
                        code="000001",
                        name="平安银行",
                        category="ir",
                        subcategory="latest_reply",
                        source="东方财富-问董秘-最新答复",
                        event_time="2026-08-13T11:00:00+08:00",
                        title="问题",
                        summary="回答",
                    )
                ],
            )
            rows = fetch_recent(str(path), "2026-08-13T00:00:00+08:00")
            self.assertEqual(rows[0]["subcategory"], "latest_reply")


class EastmoneyIrTests(unittest.TestCase):
    def test_three_sections_are_mapped_and_answerless_rows_are_skipped(self):
        tz = ZoneInfo("Asia/Shanghai")
        now = datetime.now(tz)
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        stock = Stock("300001", "测试股份", "sz")

        def fake_page(_stock, qatype, page):
            self.assertEqual(page, 1)
            common = {
                "post_id": 100 + qatype,
                "stockbar_code": stock.code,
                "stockbar_name": stock.name,
                "post_publish_time": stamp,
                "post_display_time": stamp,
                "post_title": f"标题{qatype}",
            }
            if qatype == 1:
                rows = [
                    dict(common, ask_question="普通问题", ask_answer="公司回答"),
                    dict(common, post_id=999, ask_question="未答问题", ask_answer=""),
                ]
            elif qatype == 2:
                rows = [dict(common, ask_question="传闻是否属实", ask_answer="公司求证")]
            else:
                rows = [
                    dict(
                        common,
                        post_title="公司发布材料",
                        post_content="公司发布材料正文",
                        post_pdf_url="http://pdf.example/report.pdf",
                    )
                ]
            return {"rc": 1, "re": rows, "TotalPage": 1, "PageIndex": 1}

        with patch("collectors.eastmoney_ir._fetch_page", side_effect=fake_page):
            items = collect_eastmoney_ir(
                stock,
                now - timedelta(hours=2),
                "Asia/Shanghai",
                max_pages=3,
            )

        self.assertEqual(len(items), 3)
        self.assertEqual(
            {item.subcategory for item in items},
            {"latest_reply", "rumor_verification", "company_release"},
        )
        company_release = next(i for i in items if i.subcategory == "company_release")
        self.assertTrue(company_release.url.startswith("https://"))
        self.assertEqual(company_release.summary, "公司发布材料正文")


if __name__ == "__main__":
    unittest.main()
