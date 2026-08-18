"""东方财富“问董秘”三个公开分项的采集器。"""

from __future__ import annotations

import logging
from dataclasses import asdict
from urllib.parse import urlencode

import requests

from models import Item
from utils import parse_local_datetime, safe_call, within_window

logger = logging.getLogger(__name__)

LIST_URL = "https://guba.eastmoney.com/qa/search"
API_URL = "https://guba.eastmoney.com/interface/GetData.aspx"
API_PATH = "question/api/Info/Search"

IR_SECTIONS = {
    1: "latest_reply",
    2: "rumor_verification",
    3: "company_release",
}

IR_SECTION_LABELS = {
    "latest_reply": "最新答复",
    "rumor_verification": "传闻求证",
    "company_release": "公司发布",
}


def _clean(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "nat", "none"} else text


def _page_url(stock, qatype: int) -> str:
    return f"{LIST_URL}?code={stock.code}&type={qatype}"


def _fetch_page(stock, qatype: int, page: int) -> dict:
    response = requests.post(
        API_URL,
        data={
            "path": API_PATH,
            "env": "2",
            "param": urlencode(
                {
                    "code": stock.code,
                    "ps": 15,
                    "p": page,
                    "qatype": qatype,
                }
            ),
        },
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; AStockRadar/1.1; public-data-only)"
            ),
            "Referer": _page_url(stock, qatype),
            "Accept": "application/json, text/javascript, */*; q=0.01",
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("rc") != 1:
        message = _clean(payload.get("me") or payload.get("Message"))
        if "暂无结果" in message or "暂无数据" in message:
            return {
                "rc": 1,
                "re": [],
                "TotalPage": 0,
                "PageIndex": page,
                "StockName": "",
            }
        raise RuntimeError(
            f"东方财富问董秘返回异常：{message or payload.get('error_code')}"
        )
    return payload


def _row_time(row: dict, qatype: int, tz_name: str) -> str:
    value = row.get("post_display_time") or row.get("post_publish_time")
    dt = parse_local_datetime(value, tz_name)
    if dt is None:
        return ""
    if qatype == 3 and dt.hour == dt.minute == dt.second == 0:
        dt = dt.replace(hour=18)
    return dt.isoformat(timespec="seconds")


def _detail_url(stock, row: dict, qatype: int) -> str:
    pdf_url = _clean(row.get("post_pdf_url"))
    if pdf_url:
        return pdf_url.replace("http://", "https://", 1)
    post_id = _clean(row.get("post_id"))
    if post_id:
        return f"https://guba.eastmoney.com/news,{stock.code},{post_id}.html"
    return _page_url(stock, qatype)


def _to_item(stock, row: dict, qatype: int, tz_name: str) -> Item | None:
    subcategory = IR_SECTIONS[qatype]
    event_time = _row_time(row, qatype, tz_name)
    if not event_time:
        return None

    if qatype in (1, 2):
        question = _clean(row.get("ask_question"))
        answer = _clean(row.get("ask_answer"))
        # “最新答复”和“传闻求证”只保留已有公司回复的内容。
        if not answer:
            return None
        title = question or _clean(row.get("post_title")) or "问董秘答复"
        summary = answer
    else:
        title = _clean(row.get("post_title")) or "公司发布"
        content = _clean(row.get("post_content"))
        summary = content if content != title else ""

    payload = dict(row)
    payload["eastmoney_qatype"] = qatype
    payload["eastmoney_section"] = IR_SECTION_LABELS[subcategory]
    payload["fetch_channel"] = "东方财富网-问董秘"
    return Item(
        code=stock.code,
        name=_clean(row.get("stockbar_name")) or stock.name,
        category="ir",
        subcategory=subcategory,
        source=f"东方财富网-问董秘-{IR_SECTION_LABELS[subcategory]}",
        event_time=event_time,
        title=title[:500],
        summary=summary[:3000],
        url=_detail_url(stock, row, qatype),
        payload=payload,
    )


def collect_eastmoney_ir(stock, cutoff, tz_name, max_pages=10):
    """Collect 最新答复、传闻求证、公司发布 for one stock."""
    out = []

    for qatype in IR_SECTIONS:
        for page in range(1, max(1, int(max_pages)) + 1):
            payload = safe_call(
                f"eastmoney-ir:{stock.code}:{qatype}:{page}",
                _fetch_page,
                stock,
                qatype,
                page,
            )
            rows = payload.get("re") or []
            if not rows:
                break

            parsed_times = []
            for row in rows:
                event_time = _row_time(row, qatype, tz_name)
                parsed = parse_local_datetime(event_time, tz_name)
                if parsed is not None:
                    parsed_times.append(parsed)
                if cutoff is not None and not within_window(
                    event_time, cutoff, tz_name
                ):
                    continue
                item = _to_item(stock, row, qatype, tz_name)
                if item is not None:
                    out.append(item)

            if cutoff is not None and parsed_times and min(parsed_times) < cutoff:
                break
            if page >= int(payload.get("TotalPage") or page):
                break

    return out


def query_eastmoney_ir(code: str, tz_name="Asia/Shanghai", pages=1) -> list[dict]:
    """Direct live query used by the dashboard; it ignores the 24h news window."""
    from stocks import Stock, infer_market, normalize_stock_code

    code = normalize_stock_code(code)
    stock = Stock(code, code, infer_market(code))
    return [
        asdict(item)
        for item in collect_eastmoney_ir(
            stock,
            cutoff=None,
            tz_name=tz_name,
            max_pages=pages,
        )
    ]
