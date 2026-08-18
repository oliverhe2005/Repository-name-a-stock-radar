import logging
from datetime import datetime, timedelta
from functools import lru_cache
import math

from models import Item
from utils import iso_local, parse_local_datetime, safe_call, within_window

logger = logging.getLogger(__name__)


def _ak():
    import akshare as ak
    return ak


def _records(df):
    if df is None or getattr(df, "empty", True):
        return []
    return df.to_dict("records")


def _norm_code(value):
    """Normalize stock codes to six digits."""
    if value is None:
        return ""

    text = str(value).strip()

    if text.endswith(".0"):
        text = text[:-2]

    digits = "".join(ch for ch in text if ch.isdigit())

    if not digits:
        return text

    return digits.zfill(6)[-6:]


def _to_float(value):
    try:
        x = float(value)
        if math.isnan(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def _date_strings(cutoff):
    """Calendar dates touched by the rolling window."""
    now = datetime.now(cutoff.tzinfo)

    current = cutoff.date()
    end = now.date()

    dates = []

    while current <= end:
        dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)

    return dates


def _date_event_time(value, tz_name, hour=18):
    """
    Some Eastmoney datasets only provide a date, not an exact timestamp.
    Use 18:00 local time for display/filtering of post-market datasets.
    """
    dt = parse_local_datetime(value, tz_name)

    if dt is None:
        return ""

    if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
        dt = dt.replace(hour=hour)

    return dt.isoformat(timespec="seconds")


# ============================================================
# NEWS
# ============================================================

def collect_news(stock, cutoff, tz_name):
    ak = _ak()

    df = safe_call(
        f"news:{stock.code}",
        ak.stock_news_em,
        symbol=stock.code,
    )

    out = []

    for row in _records(df):
        t = row.get("发布时间")

        if not within_window(t, cutoff, tz_name):
            continue

        out.append(
            Item(
                code=stock.code,
                name=stock.name,
                category="news",
                source="东方财富-个股新闻",
                event_time=iso_local(t, tz_name),
                title=str(row.get("新闻标题") or "").strip(),
                summary=str(row.get("新闻内容") or "").strip()[:1200],
                url=str(row.get("新闻链接") or "") or None,
                payload=row,
            )
        )

    return out


# ============================================================
# ANNOUNCEMENTS
# Pull market-wide data ONCE per date, then filter watchlist.
# ============================================================

@lru_cache(maxsize=16)
def _announcement_day(date_str):
    ak = _ak()

    return safe_call(
        f"announcement-market:{date_str}",
        ak.stock_notice_report,
        symbol="全部",
        date=date_str,
    )


def collect_announcements(stock, cutoff, tz_name):
    out = []

    for date_str in _date_strings(cutoff):
        df = _announcement_day(date_str)

        if df is None or getattr(df, "empty", True):
            continue

        if "代码" not in df.columns:
            logger.warning(
                "announcement dataset missing 代码 column: %s",
                list(df.columns),
            )
            continue

        stock_rows = df[
            df["代码"].map(_norm_code) == stock.code
        ]

        for row in _records(stock_rows):
            t = row.get("公告日期")

            event_time = _date_event_time(
                t,
                tz_name,
                hour=18,
            )

            out.append(
                Item(
                    code=stock.code,
                    name=stock.name,
                    category="announcement",
                    source="东方财富-沪深京公告",
                    event_time=event_time,
                    title=str(row.get("公告标题") or "").strip(),
                    summary=str(row.get("公告类型") or "").strip(),
                    url=str(row.get("网址") or "") or None,
                    payload=row,
                )
            )

    return out


# ============================================================
# BLOCK TRADES / 大宗交易
# 东方财富 数据中心 → 大宗交易 → 每日明细
# ============================================================

@lru_cache(maxsize=8)
def _block_trade_range(start_date, end_date):
    ak = _ak()

    return safe_call(
        f"block-trade-market:{start_date}:{end_date}",
        ak.stock_dzjy_mrmx,
        symbol="A股",
        start_date=start_date,
        end_date=end_date,
    )


def collect_block_trades(stock, cutoff, tz_name):
    now = datetime.now(cutoff.tzinfo)

    start_date = cutoff.strftime("%Y%m%d")
    end_date = now.strftime("%Y%m%d")

    df = _block_trade_range(
        start_date,
        end_date,
    )

    if df is None or getattr(df, "empty", True):
        return []

    if "证券代码" not in df.columns:
        logger.warning(
            "block trade dataset missing 证券代码 column: %s",
            list(df.columns),
        )
        return []

    stock_rows = df[
        df["证券代码"].map(_norm_code) == stock.code
    ]

    out = []

    for row in _records(stock_rows):
        trade_date = row.get("交易日期")

        amount = _to_float(row.get("成交额"))
        price = _to_float(row.get("成交价"))
        volume = _to_float(row.get("成交量"))
        premium = _to_float(row.get("折溢率"))

        title_parts = ["大宗交易"]

        if amount is not None:
            title_parts.append(
                f"{amount / 10000:,.2f} 万元"
            )

        if price is not None:
            title_parts.append(
                f"成交价 {price:g} 元"
            )

        if premium is not None:
            title_parts.append(
                f"折溢率 {premium * 100:+.2f}%"
            )

        summary_parts = []

        if volume is not None:
            summary_parts.append(
                f"成交量 {volume:,.0f} 股"
            )

        buyer = str(
            row.get("买方营业部") or ""
        ).strip()

        seller = str(
            row.get("卖方营业部") or ""
        ).strip()

        if buyer:
            summary_parts.append(
                f"买方：{buyer}"
            )

        if seller:
            summary_parts.append(
                f"卖方：{seller}"
            )

        out.append(
            Item(
                code=stock.code,
                name=stock.name,
                category="block_trade",
                source="东方财富-大宗交易",
                event_time=_date_event_time(
                    trade_date,
                    tz_name,
                    hour=18,
                ),
                title="｜".join(title_parts),
                summary="；".join(summary_parts),
                url=(
                    "https://data.eastmoney.com/"
                    "dzjy/dzjy_mrmx.html"
                ),
                payload=row,
            )
        )

    return out
