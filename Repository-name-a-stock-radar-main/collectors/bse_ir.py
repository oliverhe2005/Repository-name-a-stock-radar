"""Best-effort BSE investor-Q&A fallback.

The BSE does not currently expose, in the same way as SZSE's 互动易 or SSE's e互动,
a stable public company-Q&A API that this project can safely rely on. For BSE names,
we use the public 同花顺 i问董秘 page as a fallback only. If its page structure or
anti-bot policy changes, the collector returns no rows and the rest of the pipeline
continues normally.
"""
import logging
import re

import requests
from bs4 import BeautifulSoup

from models import Item
from utils import iso_local, within_window

logger = logging.getLogger(__name__)


def collect_bse_ir_fallback(stock, cutoff, tz_name):
    url = f"https://basic.10jqka.com.cn/{stock.code}/interactive.html"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; WatchlistMonitor/1.0; public-data-only)",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=12)
        r.raise_for_status()
    except Exception as exc:
        logger.warning("BSE IR fallback request failed for %s: %s", stock.code, exc)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text("\n", strip=True)
    # Dynamic rendering is common. Parse only when a clear Q&A/time pattern is present.
    # This intentionally avoids brittle DOM assumptions or bypassing anti-bot controls.
    date_matches = list(re.finditer(r"(?P<date>\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2})", text))
    if not date_matches:
        logger.info("No server-rendered BSE Q&A rows for %s; leaving source empty", stock.code)
        return []

    out = []
    for i, match in enumerate(date_matches[:30]):
        start = match.start()
        end = date_matches[i + 1].start() if i + 1 < len(date_matches) else min(len(text), start + 1800)
        block = text[start:end]
        stamp = f"{match.group('date')} {match.group('time')}"
        if not within_window(stamp, cutoff, tz_name):
            continue
        q_match = re.search(r"问[:：]\s*(.+?)(?:答[:：]|$)", block, re.S)
        a_match = re.search(r"答[:：]\s*(.+)$", block, re.S)
        q = re.sub(r"\s+", " ", q_match.group(1)).strip() if q_match else "北交所个股互动更新"
        a = re.sub(r"\s+", " ", a_match.group(1)).strip() if a_match else ""
        out.append(Item(
            code=stock.code,
            name=stock.name,
            category="ir",
            source="同花顺i问董秘-BSE fallback",
            event_time=iso_local(stamp, tz_name),
            title=q[:220],
            summary=a[:1800],
            url=url,
            payload={"raw": block[:2500]},
        ))
    return out
