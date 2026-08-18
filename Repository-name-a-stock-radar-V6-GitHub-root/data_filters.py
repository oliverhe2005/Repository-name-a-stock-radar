"""共享的数据可见性规则。"""

CURRENT_IR_SUBCATEGORIES = frozenset(
    {"latest_reply", "rumor_verification", "company_release"}
)

CURRENT_IR_SOURCE_PREFIXES = (
    "深交所互动易-",
    "上证e互动-",
    "全景网北交所互动-",
)


def is_current_record(row: dict) -> bool:
    """旧交易所问答保留在数据库中，但不再进入页面或日报。"""
    if row.get("category") != "ir":
        return True
    return (
        row.get("subcategory") in CURRENT_IR_SUBCATEGORIES
        and str(row.get("source") or "").startswith(
            CURRENT_IR_SOURCE_PREFIXES
        )
    )


def filter_current_records(rows) -> list[dict]:
    return [row for row in rows if is_current_record(row)]
