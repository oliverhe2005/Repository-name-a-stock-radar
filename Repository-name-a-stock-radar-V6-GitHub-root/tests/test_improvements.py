import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from collectors.eastmoney_ir import collect_eastmoney_ir
from collectors.exchange_ir import (
    _parse_sse_html,
    collect_exchange_ir,
    collect_sz_ir,
    platform_for_code,
)
from data_filters import filter_current_records, is_current_record
from db import connect, fetch_recent, upsert_items
from models import Item
from stocks import (
    Stock,
    add_watchlist_codes,
    get_watchlist,
    infer_market,
    remove_watchlist_codes,
    replace_watchlist,
    watchlist_from_json,
    watchlist_to_json,
)


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

            removed = remove_watchlist_codes("600519, 002371", path=path)
            self.assertEqual({s.code for s in removed}, {"600519", "002371"})
            remaining_codes = {s.code for s in get_watchlist(path)}
            self.assertNotIn("600519", remaining_codes)
            self.assertNotIn("002371", remaining_codes)

            restored = add_watchlist_codes(
                "002371", path=path, resolver=lambda code: code
            )
            self.assertEqual([s.code for s in restored], ["002371"])
            self.assertIn("002371", {s.code for s in get_watchlist(path)})

    def test_market_inference(self):
        self.assertEqual(infer_market("688001"), "sh")
        self.assertEqual(infer_market("300001"), "sz")
        self.assertEqual(infer_market("920001"), "bj")

    def test_export_import_and_exact_restore(self):
        stocks = [
            Stock("002371", "北方华创", "sz"),
            Stock("600519", "贵州茅台", "sh"),
        ]
        encoded = watchlist_to_json(stocks)
        decoded = watchlist_from_json(encoded)
        self.assertEqual(decoded, stocks)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom.json"
            replace_watchlist(decoded, path=path)
            restored = get_watchlist(path)
            self.assertEqual({stock.code for stock in restored}, {"002371", "600519"})


class DataFilterTests(unittest.TestCase):
    def test_legacy_exchange_ir_is_hidden_without_deleting_other_rows(self):
        legacy = {
            "category": "ir",
            "subcategory": "",
            "source": "深交所互动易",
        }
        current = {
            "category": "ir",
            "subcategory": "latest_reply",
            "source": "深交所互动易-最新答复",
        }
        old_eastmoney = {
            "category": "ir",
            "subcategory": "latest_reply",
            "source": "东方财富网-问董秘-最新答复",
        }
        news = {"category": "news", "source": "东方财富"}
        self.assertFalse(is_current_record(legacy))
        self.assertFalse(is_current_record(old_eastmoney))
        self.assertTrue(is_current_record(current))
        self.assertEqual(filter_current_records([legacy, current, news]), [current, news])


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
                        source="深交所互动易-最新答复",
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

    def test_empty_section_does_not_hide_other_sections(self):
        tz = ZoneInfo("Asia/Shanghai")
        now = datetime.now(tz)
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        stock = Stock("603986", "兆易创新", "sh")

        def fake_page(_stock, qatype, _page):
            if qatype == 2:
                return {"rc": 1, "re": [], "TotalPage": 0, "PageIndex": 1}
            row = {
                "post_id": qatype,
                "stockbar_code": stock.code,
                "stockbar_name": stock.name,
                "post_publish_time": stamp,
                "post_display_time": stamp,
                "post_title": "公司资料",
                "post_content": "公司资料正文",
                "ask_question": "投资者问题",
                "ask_answer": "公司回答",
            }
            return {"rc": 1, "re": [row], "TotalPage": 1, "PageIndex": 1}

        with patch("collectors.eastmoney_ir._fetch_page", side_effect=fake_page):
            items = collect_eastmoney_ir(stock, None, "Asia/Shanghai", max_pages=1)

        self.assertEqual(
            {item.subcategory for item in items},
            {"latest_reply", "company_release"},
        )


class ExchangeIrTests(unittest.TestCase):
    def test_platform_is_selected_from_stock_code(self):
        self.assertEqual(platform_for_code("002371")["name"], "深交所互动易")
        self.assertEqual(platform_for_code("603986")["name"], "上证e互动")
        self.assertEqual(platform_for_code("688188")["name"], "上证e互动")
        self.assertEqual(platform_for_code("920098")["name"], "全景网北交所互动专区")

    def test_sz_answered_rows_are_normalized(self):
        stock = Stock("002371", "北方华创", "sz")
        payload = {
            "rows": [
                {
                    "indexId": "123",
                    "stockCode": stock.code,
                    "companyShortName": stock.name,
                    "mainContent": "公司有何新进展？",
                    "attachedContent": "请以公告为准。",
                    "updateDate": 1787020800000,
                },
                {
                    "indexId": "124",
                    "mainContent": "尚未回复",
                    "attachedContent": "",
                    "updateDate": 1787020800000,
                },
            ],
            "totalPage": 1,
        }
        with patch("collectors.exchange_ir._sz_org_id", return_value="gssz0002371"), patch(
            "collectors.exchange_ir._sz_page", return_value=payload
        ):
            items = collect_sz_ir(stock, None, "Asia/Shanghai", 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source, "深交所互动易-最新答复")
        self.assertEqual(items[0].summary, "请以公告为准。")

    def test_sse_html_question_answer_pairs_are_parsed(self):
        stock = Stock("600031", "三一重工", "sh")
        html = """
        <div class='m_feed_txt'>:三一重工(600031) 请问股东人数？</div>
        <div class='m_feed_from'>2026年08月17日 09:00 来自 网站</div>
        <div class='m_feed_txt'>截至目前为12345户。</div>
        <div class='m_feed_from'>2026年08月18日 10:30 来自 网站</div>
        """
        items = _parse_sse_html(stock, "31", html, "Asia/Shanghai")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "请问股东人数？")
        self.assertEqual(items[0].summary, "截至目前为12345户。")
        self.assertEqual(items[0].source, "上证e互动-最新答复")

    def test_dispatch_uses_stock_market(self):
        stock = Stock("920098", "科隆新材", "bj")
        with patch("collectors.exchange_ir.collect_bj_ir", return_value=[]) as bj, patch(
            "collectors.exchange_ir.collect_sh_ir", return_value=[]
        ) as sh, patch("collectors.exchange_ir.collect_sz_ir", return_value=[]) as sz:
            collect_exchange_ir(stock, None, "Asia/Shanghai", 1)
        bj.assert_called_once()
        sh.assert_not_called()
        sz.assert_not_called()


if __name__ == "__main__":
    unittest.main()
