import logging
import time

from collectors.akshare_collectors import (
    collect_announcements,
    collect_block_trades,
    collect_news,
)
from collectors.eastmoney_ir import collect_eastmoney_ir
from config import load_config
from db import upsert_items
from stocks import get_watchlist
from utils import cutoff_time

logger = logging.getLogger(__name__)


def run_once(stocks=None):
    cfg = load_config()

    cutoff = cutoff_time(
        cfg["timezone"],
        int(cfg["lookback_hours"]),
    )

    all_items = []
    errors = []

    stocks = list(stocks) if stocks is not None else get_watchlist()

    for stock in stocks:

        collectors = [
            (
                "news",
                lambda: collect_news(
                    stock,
                    cutoff,
                    cfg["timezone"],
                ),
            ),
            (
                "announcement",
                lambda: collect_announcements(
                    stock,
                    cutoff,
                    cfg["timezone"],
                ),
            ),
            (
                "block_trade",
                lambda: collect_block_trades(
                    stock,
                    cutoff,
                    cfg["timezone"],
                ),
            ),
            (
                "ir",
                lambda: collect_eastmoney_ir(
                    stock,
                    None,
                    cfg["timezone"],
                    cfg.get("max_eastmoney_ir_pages", 1),
                ),
            ),
        ]

        for label, fn in collectors:

            try:
                items = fn()

                all_items.extend(items)

                logger.info(
                    "%s %s: %d items",
                    stock.code,
                    label,
                    len(items),
                )

            except Exception as exc:

                errors.append(
                    (
                        stock.code,
                        label,
                        str(exc),
                    )
                )

                logger.exception(
                    "collector failed: %s %s",
                    stock.code,
                    label,
                )

            time.sleep(
                float(
                    cfg.get(
                        "request_interval_seconds",
                        0.8,
                    )
                )
            )

    inserted, ignored = upsert_items(
        cfg["database_path"],
        all_items,
    )

    return {
        "collected": len(all_items),
        "inserted": inserted,
        "duplicates": ignored,
        "errors": errors,
        "cutoff": cutoff.isoformat(
            timespec="seconds"
        ),
    }
