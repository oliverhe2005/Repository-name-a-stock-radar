"""按上市市场查询官方/指定的投资者互动平台。"""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from models import Item
from stocks import Stock, infer_market, normalize_stock_code
from utils import parse_local_datetime, safe_call, within_window

USER_AGENT = "Mozilla/5.0 (compatible; AStockRadar/1.2; public-data-only)"

PLATFORM_INFO = {
    "sz": {
        "name": "深交所互动易",
        "url": "https://irm.cninfo.com.cn/",
    },
    "sh": {
        "name": "上证e互动",
        "url": "https://sns.sseinfo.com/index.do",
    },
    "bj": {
        "name": "全景网北交所互动专区",
        "url": "https://ir.p5w.net/question/",
    },
}


def platform_for_code(code: str) -> dict[str, str]:
    return PLATFORM_INFO[infer_market(code)].copy()


def _clean(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "nat", "none"} else text


def _iso_time(value, tz_name: str) -> str:
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and value.strip().isdigit() and len(value.strip()) >= 12
    ):
        try:
            value = datetime.fromtimestamp(
                float(value) / 1000,
                tz=timezone.utc,
            )
        except (TypeError, ValueError, OSError):
            pass
    dt = parse_local_datetime(value, tz_name)
    return dt.isoformat(timespec="seconds") if dt else ""


def _keep_item(item: Item | None, cutoff, tz_name: str) -> bool:
    return bool(
        item
        and (cutoff is None or within_window(item.event_time, cutoff, tz_name))
    )


# 深交所互动易 ---------------------------------------------------------------


def _sz_org_id(code: str) -> str:
    response = requests.post(
        "https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo",
        params={"_t": "1691144074"},
        data={"keyWord": code},
        headers={"User-Agent": USER_AGENT, "Referer": PLATFORM_INFO["sz"]["url"]},
        timeout=15,
    )
    response.raise_for_status()
    rows = response.json().get("data") or []
    match = next(
        (row for row in rows if _clean(row.get("stockcode") or row.get("code")) == code),
        rows[0] if rows else None,
    )
    if not match or not match.get("secid"):
        raise RuntimeError(f"深交所互动易找不到股票 {code}")
    return str(match["secid"])


def _sz_page(code: str, org_id: str, page: int, page_size: int = 50) -> dict:
    response = requests.post(
        "https://irm.cninfo.com.cn/newircs/company/question",
        params={
            "_t": "1691142650",
            "stockcode": code,
            "orgId": org_id,
            "pageSize": page_size,
            "pageNum": page,
            "keyWord": "",
            "startDay": "",
            "endDay": "",
        },
        headers={"User-Agent": USER_AGENT, "Referer": PLATFORM_INFO["sz"]["url"]},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def _sz_item(stock: Stock, row: dict, tz_name: str) -> Item | None:
    answer = _clean(row.get("attachedContent"))
    if not answer:
        return None
    question = _clean(row.get("mainContent")) or "投资者提问"
    question_id = _clean(row.get("indexId"))
    event_time = _iso_time(
        row.get("updateDate") or row.get("attachedPubDate") or row.get("pubDate"),
        tz_name,
    )
    if not event_time:
        return None
    url = PLATFORM_INFO["sz"]["url"]
    if question_id:
        url = (
            "https://irm.cninfo.com.cn/ircs/question/questionDetail"
            f"?questionId={question_id}"
        )
    payload = dict(row)
    payload["fetch_channel"] = PLATFORM_INFO["sz"]["name"]
    return Item(
        code=stock.code,
        name=_clean(row.get("companyShortName")) or stock.name,
        category="ir",
        subcategory="latest_reply",
        source="深交所互动易-最新答复",
        event_time=event_time,
        title=question[:500],
        summary=answer[:3000],
        url=url,
        payload=payload,
    )


def collect_sz_ir(stock: Stock, cutoff, tz_name: str, max_pages=1) -> list[Item]:
    org_id = safe_call(f"sz-ir-org:{stock.code}", _sz_org_id, stock.code)
    out = []
    for page in range(1, max(1, int(max_pages)) + 1):
        payload = safe_call(
            f"sz-ir:{stock.code}:{page}", _sz_page, stock.code, org_id, page
        )
        rows = payload.get("rows") or []
        if not rows:
            break
        for row in rows:
            item = _sz_item(stock, row, tz_name)
            if _keep_item(item, cutoff, tz_name):
                out.append(item)
        if page >= int(payload.get("totalPage") or page):
            break
    return out


# 上证 e 互动 ---------------------------------------------------------------


def _sse_uid(code: str) -> str:
    response = requests.post(
        "https://sns.sseinfo.com/ajax/getCompany.do",
        data={"data": code},
        headers={
            "User-Agent": USER_AGENT,
            "Referer": PLATFORM_INFO["sh"]["url"],
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=15,
    )
    response.raise_for_status()
    uid = response.text.strip().strip('"')
    if not uid or not re.fullmatch(r"\d+", uid):
        raise RuntimeError(f"上证e互动找不到股票 {code}")
    return uid


def _sse_page(uid: str, page: int, page_size: int = 100) -> str:
    response = requests.post(
        "https://sns.sseinfo.com/ajax/userfeeds.do",
        params={
            "typeCode": "company",
            "type": "11",
            "pageSize": page_size,
            "uid": uid,
            "page": page,
        },
        headers={
            "User-Agent": USER_AGENT,
            "Referer": f"https://sns.sseinfo.com/company.do?uid={uid}",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.text


def _sse_time(text: str, tz_name: str) -> str:
    text = _clean(text).split("来自", 1)[0].strip()
    text = text.replace("年", "-").replace("月", "-").replace("日", "")
    return _iso_time(text, tz_name)


def _parse_sse_html(stock: Stock, uid: str, html: str, tz_name: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    texts = [node.get_text(" ", strip=True) for node in soup.select(".m_feed_txt")]
    times = [node.get_text(" ", strip=True) for node in soup.select(".m_feed_from")]
    out = []
    for index in range(0, len(texts) - 1, 2):
        raw_question, answer = texts[index], texts[index + 1]
        if not _clean(answer):
            continue
        match = re.match(r"^:?\s*(.*?)\((\d{6})\)\s*(.*)$", raw_question)
        if match:
            name, code, question = match.groups()
        else:
            name, code, question = stock.name, stock.code, raw_question.lstrip(": ")
        if code != stock.code:
            continue
        event_time = _sse_time(
            times[index + 1] if index + 1 < len(times) else "", tz_name
        )
        if not event_time:
            continue
        out.append(
            Item(
                code=stock.code,
                name=_clean(name) or stock.name,
                category="ir",
                subcategory="latest_reply",
                source="上证e互动-最新答复",
                event_time=event_time,
                title=_clean(question)[:500] or "投资者提问",
                summary=_clean(answer)[:3000],
                url=f"https://sns.sseinfo.com/company.do?uid={uid}",
                payload={
                    "question_time": times[index] if index < len(times) else "",
                    "reply_time": times[index + 1] if index + 1 < len(times) else "",
                    "fetch_channel": PLATFORM_INFO["sh"]["name"],
                },
            )
        )
    return out


def collect_sh_ir(stock: Stock, cutoff, tz_name: str, max_pages=1) -> list[Item]:
    uid = safe_call(f"sh-ir-uid:{stock.code}", _sse_uid, stock.code)
    out = []
    for page in range(1, max(1, int(max_pages)) + 1):
        html = safe_call(f"sh-ir:{stock.code}:{page}", _sse_page, uid, page)
        if "暂无回复" in html:
            break
        items = _parse_sse_html(stock, uid, html, tz_name)
        if not items:
            break
        out.extend(item for item in items if _keep_item(item, cutoff, tz_name))
    return out


# 全景网北交所互动专区 --------------------------------------------------------


def _p5w_company(code: str) -> dict:
    response = requests.post(
        "https://ir.p5w.net/company/validCompanyJson.shtml",
        data={"keyword": code, "companyType": ""},
        headers={
            "User-Agent": USER_AGENT,
            "Referer": PLATFORM_INFO["bj"]["url"],
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=15,
    )
    response.raise_for_status()
    rows = response.json().get("obj") or []
    match = next((row for row in rows if _clean(row.get("companyCode")) == code), None)
    if not match or not match.get("pid"):
        raise RuntimeError(f"全景网北交所互动专区找不到股票 {code}")
    return match


def _p5w_page(company: dict, page: int, page_size: int = 20) -> dict:
    response = requests.post(
        "https://ir.p5w.net/interaction/getNewSearchR.shtml",
        data={
            "isPagination": "1",
            "keyWords": "",
            "companyCode": company["companyCode"],
            "companyBaseinfoId": company["pid"],
            "page": page,
            "rows": page_size,
        },
        headers={
            "User-Agent": USER_AGENT,
            "Referer": PLATFORM_INFO["bj"]["url"],
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def _p5w_item(stock: Stock, row: dict, tz_name: str) -> Item | None:
    answer = _clean(row.get("replyContent"))
    if not answer:
        return None
    event_time = _iso_time(row.get("replyerTimeStr"), tz_name)
    if not event_time:
        return None
    question_id = _clean(row.get("pid"))
    url = (
        f"https://ir.p5w.net/question/{question_id}.shtml"
        if question_id
        else PLATFORM_INFO["bj"]["url"]
    )
    payload = dict(row)
    payload["fetch_channel"] = PLATFORM_INFO["bj"]["name"]
    return Item(
        code=stock.code,
        name=_clean(row.get("companyShortname")) or stock.name,
        category="ir",
        subcategory="latest_reply",
        source="全景网北交所互动-最新答复",
        event_time=event_time,
        title=_clean(row.get("content"))[:500] or "投资者提问",
        summary=answer[:3000],
        url=url,
        payload=payload,
    )


def collect_bj_ir(stock: Stock, cutoff, tz_name: str, max_pages=1) -> list[Item]:
    company = safe_call(f"bj-ir-company:{stock.code}", _p5w_company, stock.code)
    out = []
    page_size = 20
    for page in range(1, max(1, int(max_pages)) + 1):
        payload = safe_call(
            f"bj-ir:{stock.code}:{page}", _p5w_page, company, page, page_size
        )
        rows = payload.get("rows") or []
        if not rows:
            break
        for row in rows:
            item = _p5w_item(stock, row, tz_name)
            if _keep_item(item, cutoff, tz_name):
                out.append(item)
        if page * page_size >= int(payload.get("total") or 0):
            break
    return out


def collect_exchange_ir(stock: Stock, cutoff, tz_name: str, max_pages=1) -> list[Item]:
    """Dispatch one stock to its required interaction platform."""
    collectors = {"sz": collect_sz_ir, "sh": collect_sh_ir, "bj": collect_bj_ir}
    market = stock.market if stock.market in collectors else infer_market(stock.code)
    return collectors[market](stock, cutoff, tz_name, max_pages)


def query_exchange_ir(code: str, tz_name="Asia/Shanghai", pages=1) -> list[dict]:
    """Live dashboard query, independent from the news time window."""
    code = normalize_stock_code(code)
    stock = Stock(code, code, infer_market(code))
    return [
        asdict(item)
        for item in collect_exchange_ir(
            stock,
            cutoff=None,
            tz_name=tz_name,
            max_pages=pages,
        )
    ]
