from collections import defaultdict
from pathlib import Path

from config import load_config
from db import fetch_recent
from stocks import get_watchlist
from utils import cutoff_time, now_tz


def generate_report() -> Path:
    cfg = load_config()
    cutoff = cutoff_time(cfg["timezone"], cfg["lookback_hours"])
    rows = fetch_recent(cfg["database_path"], cutoff.isoformat(timespec="seconds"))
    grouped = defaultdict(lambda: defaultdict(list))
    for r in rows:
        group_key = r.get("subcategory") or r["category"]
        grouped[r["code"]][group_key].append(r)

    now = now_tz(cfg["timezone"])
    lines = [
        f"# 自选股 24h 日报 — {now:%Y-%m-%d %H:%M}",
        "",
        f"窗口：{cutoff:%Y-%m-%d %H:%M} → {now:%Y-%m-%d %H:%M}",
        "",
    ]
    labels = {
        "announcement": "公告",
        "news": "资讯",
        "block_trade": "大宗交易",
        "latest_reply": "问董秘 · 最新答复",
        "rumor_verification": "问董秘 · 传闻求证",
        "company_release": "问董秘 · 公司发布",
        "ir": "历史问董秘数据",
    }
    for stock in get_watchlist():
        cats = grouped.get(stock.code, {})
        if not any(cats.values()):
            continue
        lines += [f"## {stock.name} ({stock.code})", ""]
        for cat in [
            "announcement",
            "news",
            "block_trade",
            "latest_reply",
            "rumor_verification",
            "company_release",
            "ir",
        ]:
            items = cats.get(cat, [])
            if not items:
                continue
            lines.append(f"### {labels[cat]}")
            for r in items[:20]:
                link = f" [原文]({r['url']})" if r.get("url") else ""
                lines.append(f"- **{r['event_time'][5:16]}** {r['title']}{link}")
                if r.get("summary"):
                    lines.append(f"  - {r['summary'][:500]}")
            lines.append("")

    report_dir = Path(cfg["report_dir"])
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{now:%Y-%m-%d}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
