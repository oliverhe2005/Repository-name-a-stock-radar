import hashlib
import json
import logging
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def now_tz(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def cutoff_time(tz_name: str, hours: int) -> datetime:
    return now_tz(tz_name) - timedelta(hours=hours)


def parse_local_datetime(value, tz_name: str) -> datetime | None:
    if value is None:
        return None
    tz = ZoneInfo(tz_name)
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        if not text or text.lower() in {"nan", "nat", "none"}:
            return None
        text = text.replace("/", "-")
        candidates = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%m-%d %H:%M",
        ]
        dt = None
        for fmt in candidates:
            try:
                dt = datetime.strptime(text, fmt)
                if fmt == "%m-%d %H:%M":
                    dt = dt.replace(year=now_tz(tz_name).year)
                break
            except ValueError:
                continue
        if dt is None:
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def iso_local(value, tz_name: str) -> str:
    dt = parse_local_datetime(value, tz_name)
    if dt is None:
        return ""
    return dt.isoformat(timespec="seconds")


def within_window(value, cutoff: datetime, tz_name: str) -> bool:
    dt = parse_local_datetime(value, tz_name)
    return bool(dt and dt >= cutoff)


def fingerprint(*parts) -> str:
    raw = "|".join("" if p is None else str(p).strip() for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def safe_call(label, fn, *args, attempts=3, base_sleep=1.2, **kwargs):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # external data sources are intentionally isolated
            last = exc
            logger.warning("%s failed (%s/%s): %s", label, attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(base_sleep * attempt)
    raise last


def json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
