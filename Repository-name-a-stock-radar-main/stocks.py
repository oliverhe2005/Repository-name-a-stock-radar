import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Stock:
    code: str
    name: str
    market: str  # sh / sz / bj

    @property
    def ticker(self) -> str:
        return f"{self.market}{self.code}"


WATCHLIST = [
    Stock("002371", "北方华创", "sz"),
    Stock("603268", "松发股份", "sh"),
    Stock("688188", "柏楚电子", "sh"),
    Stock("002156", "通富微电", "sz"),
    Stock("688017", "绿的谐波", "sh"),
    Stock("300620", "光库科技", "sz"),
    Stock("688825", "长鑫科技", "sh"),
    Stock("300373", "扬杰科技", "sz"),
    Stock("688020", "方邦股份", "sh"),
    Stock("002463", "沪电股份", "sz"),
    Stock("002851", "麦格米特", "sz"),
    Stock("920045", "蘅东光", "bj"),
    Stock("300394", "天孚通信", "sz"),
    Stock("002384", "东山精密", "sz"),
    Stock("300502", "新易盛", "sz"),
    Stock("300750", "宁德时代", "sz"),
    Stock("301488", "豪恩汽电", "sz"),
    Stock("603986", "兆易创新", "sh"),
]

WATCHLIST_BY_CODE = {s.code: s for s in WATCHLIST}


def normalize_stock_code(value: str) -> str:
    """Return a six-digit A-share code or raise a helpful error."""
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", str(value).strip())
    if not match:
        raise ValueError(f"无效股票代码：{value!r}（请输入 6 位 A 股代码）")
    return match.group(1)


def parse_stock_codes(value: str) -> list[str]:
    """Parse comma/space/newline-separated six-digit stock codes."""
    codes = re.findall(r"(?<!\d)\d{6}(?!\d)", value or "")
    return list(dict.fromkeys(codes))


def infer_market(code: str) -> str:
    code = normalize_stock_code(code)
    if code[0] in {"4", "8", "9"}:
        return "bj"
    if code[0] in {"5", "6"}:
        return "sh"
    return "sz"


def _custom_watchlist_path(path=None) -> Path:
    if path is not None:
        return Path(path)
    configured = os.environ.get("WATCHLIST_FILE")
    return Path(configured) if configured else ROOT / "data" / "custom_watchlist.json"


def load_custom_watchlist(path=None) -> list[Stock]:
    watchlist_path = _custom_watchlist_path(path)
    if not watchlist_path.exists():
        return []
    try:
        rows = json.loads(watchlist_path.read_text(encoding="utf-8"))
        return [
            Stock(
                normalize_stock_code(row["code"]),
                str(row.get("name") or row["code"]).strip(),
                row.get("market") or infer_market(row["code"]),
            )
            for row in rows
        ]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("invalid custom watchlist %s: %s", watchlist_path, exc)
        return []


def get_watchlist(path=None) -> list[Stock]:
    """Load the built-in list plus stocks added from the dashboard."""
    stocks = {stock.code: stock for stock in WATCHLIST}
    for stock in load_custom_watchlist(path):
        stocks[stock.code] = stock
    return list(stocks.values())


def resolve_stock_name(code: str) -> str:
    """Best-effort name lookup; code is a safe fallback when the source is down."""
    try:
        import akshare as ak

        df = ak.stock_individual_info_em(symbol=code)
        if df is not None and not df.empty:
            item_col = "item" if "item" in df.columns else "项目"
            value_col = "value" if "value" in df.columns else "值"
            if item_col in df.columns and value_col in df.columns:
                values = df.loc[
                    df[item_col].astype(str).isin(["股票简称", "证券简称"]),
                    value_col,
                ]
                if not values.empty and str(values.iloc[0]).strip():
                    return str(values.iloc[0]).strip()
    except Exception as exc:  # an unavailable quote source must not block adding a code
        logger.warning("stock name lookup failed for %s: %s", code, exc)
    return code


def add_watchlist_codes(value: str, path=None, resolver=None) -> list[Stock]:
    """Persist new dashboard watchlist codes and return the newly added stocks."""
    codes = parse_stock_codes(value)
    if not codes:
        raise ValueError("请输入至少一个 6 位 A 股代码")

    watchlist_path = _custom_watchlist_path(path)
    custom = {stock.code: stock for stock in load_custom_watchlist(watchlist_path)}
    existing = {stock.code for stock in WATCHLIST} | set(custom)
    name_resolver = resolver or resolve_stock_name
    added = []

    for code in codes:
        if code in existing:
            continue
        stock = Stock(code, name_resolver(code) or code, infer_market(code))
        custom[code] = stock
        existing.add(code)
        added.append(stock)

    if added:
        watchlist_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = watchlist_path.with_suffix(watchlist_path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(
                [asdict(stock) for stock in custom.values()],
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temp_path.replace(watchlist_path)

    return added
