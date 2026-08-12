import logging
import re
from datetime import datetime

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


def collect_news(stock, cutoff, tz_name):
    ak = _ak()
    df = safe_call(f"news:{stock.code}", ak.stock_news_em, symbol=stock.code)
    out = []
    for row in _records(df):
        t = row.get("发布时间")
        if not within_window(t, cutoff, tz_name):
            continue
        out.append(Item(
            code=stock.code,
            name=stock.name,
            category="news",
            source="东方财富-个股新闻",
            event_time=iso_local(t, tz_name),
            title=str(row.get("新闻标题") or "").strip(),
            summary=str(row.get("新闻内容") or "").strip()[:1200],
            url=str(row.get("新闻链接") or "") or None,
            payload=row,
        ))
    return out


def collect_announcements(stock, cutoff, tz_name):
    ak = _ak()
    begin = cutoff.strftime("%Y%m%d")
    end = datetime.now(cutoff.tzinfo).strftime("%Y%m%d")
    df = safe_call(
        f"announcement:{stock.code}",
        ak.stock_individual_notice_report,
        security=stock.code,
        symbol="全部",
        begin_date=begin,
        end_date=end,
    )
    out = []
    for row in _records(df):
        t = row.get("公告日期")
        # Announcement APIs often expose date granularity only. Keep cutoff-date records.
        dt = parse_local_datetime(t, tz_name)
        if not dt or dt.date() < cutoff.date():
            continue
        out.append(Item(
            code=stock.code,
            name=stock.name,
            category="announcement",
            source="东方财富-沪深京公告",
            event_time=iso_local(t, tz_name),
            title=str(row.get("公告标题") or "").strip(),
            summary=str(row.get("公告类型") or "").strip(),
            url=str(row.get("网址") or "") or None,
            payload=row,
        ))
    return out


def collect_eastmoney_fund_flow(stock, cutoff, tz_name):
    ak = _ak()
    df = safe_call(
        f"fund_flow:{stock.code}",
        ak.stock_individual_fund_flow,
        stock=stock.code,
        market=stock.market,
    )
    rows = _records(df)
    out = []
    for row in rows:
        t = row.get("日期")
        dt = parse_local_datetime(t, tz_name)
        if not dt or dt.date() < cutoff.date():
            continue
        large = float(row.get("大单净流入-净额") or 0)
        ultra = float(row.get("超大单净流入-净额") or 0)
        main = float(row.get("主力净流入-净额") or 0)
        title = f"主力净流入 {main/1e6:+.1f} 百万元｜超大单 {ultra/1e6:+.1f}｜大单 {large/1e6:+.1f}"
        summary = (
            f"收盘价 {row.get('收盘价')}；涨跌幅 {row.get('涨跌幅')}%；"
            f"主力净占比 {row.get('主力净流入-净占比')}%；"
            f"超大单净占比 {row.get('超大单净流入-净占比')}%；"
            f"大单净占比 {row.get('大单净流入-净占比')}%"
        )
        out.append(Item(
            code=stock.code,
            name=stock.name,
            category="fund_flow",
            source="东方财富-个股资金流",
            event_time=iso_local(t, tz_name),
            title=title,
            summary=summary,
            url=f"https://data.eastmoney.com/zjlx/{stock.code}.html",
            payload=row,
        ))
    return out


def _tick_function(ak):
    # AKShare documentation/examples have used both names across versions.
    return getattr(ak, "stock_zh_a_tick_tx_js", None) or getattr(ak, "stock_zh_a_tick_tx", None)


def collect_tick_big_trades(stock, cutoff, tz_name, threshold_cny, max_rows=100):
    if stock.market not in {"sh", "sz"}:
        return []
    ak = _ak()
    fn = _tick_function(ak)
    if fn is None:
        raise RuntimeError("Current AKShare build has no Tencent A-share tick interface")
    df = safe_call(f"tick:{stock.code}", fn, symbol=stock.ticker)
    rows = []
    today = datetime.now(cutoff.tzinfo).strftime("%Y-%m-%d")
    for row in _records(df):
        amount = row.get("成交额")
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            continue
        if amount < threshold_cny:
            continue
        time_text = str(row.get("成交时间") or "").strip()
        full_time = f"{today} {time_text}" if re.match(r"^\d{2}:\d{2}", time_text) else time_text
        if not within_window(full_time, cutoff, tz_name):
            continue
        rows.append((amount, row, full_time))
    rows.sort(key=lambda x: x[0], reverse=True)
    out = []
    for amount, row, full_time in rows[:max_rows]:
        out.append(Item(
            code=stock.code,
            name=stock.name,
            category="big_trade",
            source="腾讯逐笔成交-AKShare",
            event_time=iso_local(full_time, tz_name),
            title=f"{row.get('性质') or '成交'} {amount/1e6:.2f} 百万元 @ {row.get('成交价格')}",
            summary=f"成交量 {row.get('成交量')} 手；价格变动 {row.get('价格变动')}",
            url=f"https://gu.qq.com/{stock.ticker}/gp/detail",
            payload=row,
        ))
    return out


def collect_ir(stock, cutoff, tz_name):
    ak = _ak()
    out = []
    if stock.market == "sz":
        df = safe_call(f"irm:{stock.code}", ak.stock_irm_cninfo, symbol=stock.code)
        for row in _records(df):
            t = row.get("更新时间") or row.get("提问时间")
            if not within_window(t, cutoff, tz_name):
                continue
            q = str(row.get("问题") or "").strip()
            a = str(row.get("回答内容") or "").strip()
            out.append(Item(
                stock.code, stock.name, "ir", "深交所互动易",
                iso_local(t, tz_name), q[:220] or "互动易更新", a[:1800],
                "https://irm.cninfo.com.cn/", row,
            ))
    elif stock.market == "sh":
        df = safe_call(f"einteraction:{stock.code}", ak.stock_sns_sseinfo, symbol=stock.code)
        for row in _records(df):
            t = row.get("回答时间") or row.get("问题时间")
            if not within_window(t, cutoff, tz_name):
                continue
            q = str(row.get("问题") or "").strip()
            a = str(row.get("回答") or "").strip()
            out.append(Item(
                stock.code, stock.name, "ir", "上证e互动",
                iso_local(t, tz_name), q[:220] or "上证e互动更新", a[:1800],
                "https://sns.sseinfo.com/", row,
            ))
    return out
