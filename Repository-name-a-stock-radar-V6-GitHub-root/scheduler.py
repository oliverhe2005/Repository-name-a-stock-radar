import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config import load_config
from daily_job import generate_report, run_once

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.FileHandler("watchlist.log", encoding="utf-8"), logging.StreamHandler()],
)


def job():
    result = run_once()
    path = generate_report()
    logging.info("daily run complete: %s report=%s", result, path)


if __name__ == "__main__":
    cfg = load_config()
    hour, minute = map(int, cfg.get("schedule", "17:10").split(":"))
    scheduler = BlockingScheduler(timezone=cfg["timezone"])
    scheduler.add_job(
        job,
        CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=cfg["timezone"]),
        id="watchlist_daily_scan",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    logging.info("scheduler started: weekdays %02d:%02d %s", hour, minute, cfg["timezone"])
    scheduler.start()
