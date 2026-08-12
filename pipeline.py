import logging
import time

from collectors.akshare_collectors import (
    collect_announcements,
    collect_eastmoney_fund_flow,
    collect_ir,
    collect_news,
    collect_tick_big_trades,
)
from collectors.bse_ir import collect_bse_ir_fallback
from config import load_config
from db import upsert_items
from stocks import WATCHLIST
from utils import cutoff_time

logger = logging.getLogger(__name__)


def run_once():
    cfg = load_config()
    cutoff = cutoff_time(cfg["timezone"], int(cfg["lookback_hours"]))
    all_items = []
    errors = []

    for stock in WATCHLIST:
        collectors = [
            ("news", lambda: collect_news(stock, cutoff, cfg["timezone"])),
            ("announcement", lambda: collect_announcements(stock, cutoff, cfg["timezone"])),
        ]
        if cfg.get("enable_eastmoney_fund_flow", True):
            collectors.append(("fund_flow", lambda: collect_eastmoney_fund_flow(stock, cutoff, cfg["timezone"])))
        if cfg.get("enable_tick_big_trades", True):
            collectors.append(("big_trade", lambda: collect_tick_big_trades(
                stock,
                cutoff,
                cfg["timezone"],
                float(cfg["big_trade_threshold_cny"]),
                int(cfg.get("max_big_trades_per_stock", 100)),
            )))
        collectors.append(("ir", lambda: collect_ir(stock, cutoff, cfg["timezone"])))
        if stock.market == "bj" and cfg.get("enable_bse_ir_fallback", True):
            collectors.append(("bse_ir_fallback", lambda: collect_bse_ir_fallback(stock, cutoff, cfg["timezone"])))

        for label, fn in collectors:
            try:
                items = fn()
                all_items.extend(items)
                logger.info("%s %s: %d items", stock.code, label, len(items))
            except Exception as exc:
                errors.append((stock.code, label, str(exc)))
                logger.exception("collector failed: %s %s", stock.code, label)
            time.sleep(float(cfg.get("request_interval_seconds", 0.8)))

    inserted, ignored = upsert_items(cfg["database_path"], all_items)
    return {
        "collected": len(all_items),
        "inserted": inserted,
        "duplicates": ignored,
        "errors": errors,
        "cutoff": cutoff.isoformat(timespec="seconds"),
    }
