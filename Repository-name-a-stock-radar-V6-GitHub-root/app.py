from datetime import timedelta
import json

import pandas as pd
import streamlit as st

from collectors.exchange_ir import platform_for_code, query_exchange_ir
from config import load_config
from data_filters import CURRENT_IR_SUBCATEGORIES, filter_current_records
from db import fetch_recent
from pipeline import run_once
from report import generate_report
from stocks import (
    Stock,
    WATCHLIST,
    add_watchlist_codes,
    get_watchlist,
    infer_market,
    normalize_stock_code,
    parse_stock_codes,
    replace_watchlist,
    remove_watchlist_codes,
    resolve_stock_name,
    watchlist_from_json,
    watchlist_to_json,
)
from utils import now_tz

APP_VERSION = "V6 · 三市场问董秘分流版"


@st.cache_data(ttl=300, show_spinner=False)
def load_exchange_ir_live(code: str, timezone: str):
    return query_exchange_ir(code, tz_name=timezone, pages=1)


def block_trade_details(rows: pd.DataFrame) -> pd.DataFrame:
    """Expand stored Eastmoney payloads into one visible row per trade."""
    details = []
    for row in rows.to_dict("records"):
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}

        def number(key):
            try:
                return float(payload.get(key))
            except (TypeError, ValueError):
                return None

        amount = number("成交额")
        volume = number("成交量")
        premium = number("折溢率")
        details.append(
            {
                "交易日期": str(row.get("event_time") or "")[:10],
                "股票": row.get("name") or payload.get("证券简称") or "",
                "代码": row.get("code") or payload.get("证券代码") or "",
                "成交额（万元）": amount / 10000 if amount is not None else None,
                "成交价（元）": number("成交价"),
                "成交量（万股）": volume / 10000 if volume is not None else None,
                "折溢率（%）": premium * 100 if premium is not None else None,
                "买方营业部": payload.get("买方营业部") or "",
                "卖方营业部": payload.get("卖方营业部") or "",
                "数据源": row.get("source") or "东方财富-大宗交易",
                "原文": row.get("url") or "",
            }
        )
    return pd.DataFrame(details)

st.set_page_config(page_title="A股自选股资讯雷达", layout="wide")
cfg = load_config()
now = now_tz(cfg["timezone"])

st.title("A股自选股资讯雷达")
st.caption(
    f"{APP_VERSION}｜资讯 / 公告 / 大宗交易默认按时间窗口；"
    "问董秘按深市、沪市、北交所自动选择平台"
)

session_watchlist = st.session_state.setdefault("session_watchlist", {})
session_removed = set(st.session_state.setdefault("session_removed", []))
watchlist_by_code = {stock.code: stock for stock in get_watchlist()}
for code, name in session_watchlist.items():
    watchlist_by_code[code] = Stock(code, name or code, infer_market(code))
for code in session_removed:
    watchlist_by_code.pop(code, None)
watchlist = list(watchlist_by_code.values())

with st.sidebar:
    st.caption(APP_VERSION)
    hours = st.selectbox("时间窗口", [6, 12, 24, 48, 72], index=2)
    stock_options = {f"{s.name} ({s.code})": s.code for s in watchlist}
    if st.session_state.pop("reset_stock_selection", False):
        st.session_state.pop("selected_stocks", None)
    selected_labels = st.multiselect(
        "显示/查询股票（取消勾选只隐藏）",
        list(stock_options),
        default=list(stock_options),
        key="selected_stocks",
    )
    selected_codes = [stock_options[x] for x in selected_labels]

    st.subheader("自选股管理")
    with st.form("add_watchlist_form", clear_on_submit=True):
        new_codes = st.text_area(
            "输入要增加的股票代码",
            placeholder="例如：600519, 000858\n可用逗号、空格或换行分隔",
            height=90,
        )
        add_submitted = st.form_submit_button("增加并查询", width="stretch")
    if add_submitted:
        try:
            codes = parse_stock_codes(new_codes)
            if not codes:
                raise ValueError("请输入至少一个 6 位 A 股代码")

            current_session = dict(st.session_state["session_watchlist"])
            resolved_names = {}
            for code in codes:
                existing = watchlist_by_code.get(code)
                resolved_names[code] = (
                    existing.name if existing else resolve_stock_name(code)
                )
                current_session[code] = resolved_names[code]
            st.session_state["session_watchlist"] = current_session
            removed_session = set(st.session_state["session_removed"])
            removed_session.difference_update(codes)
            st.session_state["session_removed"] = sorted(removed_session)

            # Local/server deployments get file persistence. On read-only cloud
            # filesystems the session list still works immediately.
            try:
                add_watchlist_codes(
                    new_codes,
                    resolver=lambda code: resolved_names.get(code, code),
                )
            except Exception:
                pass

            st.session_state["ir_query_code"] = codes[0]
            st.session_state["ir_code_input"] = codes[0]
            st.session_state["reset_stock_selection"] = True
            st.session_state["watchlist_flash"] = (
                "已增加：" + "、".join(
                    f"{resolved_names[code]} ({code})" for code in codes
                )
            )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    remove_options = {f"{s.name} ({s.code})": s.code for s in watchlist}
    with st.form("remove_watchlist_form", clear_on_submit=True):
        remove_labels = st.multiselect(
            "选择要删除的自选股",
            list(remove_options),
        )
        remove_submitted = st.form_submit_button("删除所选股票", width="stretch")
    if remove_submitted:
        remove_codes = [remove_options[label] for label in remove_labels]
        if not remove_codes:
            st.error("请先选择要删除的股票")
        else:
            current_session = dict(st.session_state["session_watchlist"])
            removed_session = set(st.session_state["session_removed"])
            for code in remove_codes:
                current_session.pop(code, None)
                removed_session.add(code)
            st.session_state["session_watchlist"] = current_session
            st.session_state["session_removed"] = sorted(removed_session)
            try:
                remove_watchlist_codes(",".join(remove_codes))
            except Exception:
                pass
            st.session_state["reset_stock_selection"] = True
            st.session_state["watchlist_flash"] = (
                "已删除：" + "、".join(remove_codes)
            )
            st.rerun()

    if "watchlist_flash" in st.session_state:
        st.success(st.session_state.pop("watchlist_flash"))

    with st.expander("备份 / 恢复自选股"):
        st.caption("云端重启前可先导出；重启后导入即可恢复相同列表。")
        st.download_button(
            "导出当前自选股备份",
            data=watchlist_to_json(watchlist),
            file_name="stock_watchlist.json",
            mime="application/json",
            width="stretch",
        )
        with st.form("import_watchlist_form", clear_on_submit=True):
            backup_file = st.file_uploader(
                "选择自选股备份",
                type=["json"],
            )
            import_submitted = st.form_submit_button(
                "导入并恢复此列表",
                width="stretch",
            )
        if import_submitted:
            if backup_file is None:
                st.error("请先选择 stock_watchlist.json 备份文件")
            else:
                try:
                    imported = watchlist_from_json(backup_file.getvalue())
                    imported_codes = {stock.code for stock in imported}
                    st.session_state["session_watchlist"] = {
                        stock.code: stock.name for stock in imported
                    }
                    st.session_state["session_removed"] = sorted(
                        {stock.code for stock in WATCHLIST} - imported_codes
                    )
                    try:
                        replace_watchlist(imported)
                    except Exception:
                        pass
                    st.session_state["reset_stock_selection"] = True
                    st.session_state["watchlist_flash"] = (
                        f"已从备份恢复 {len(imported)} 只股票"
                    )
                    st.rerun()
                except (UnicodeDecodeError, ValueError) as exc:
                    st.error(str(exc))

    if st.button("立即刷新数据", type="primary", width="stretch"):
        with st.spinner("正在抓取公开数据源…"):
            result = run_once(watchlist)
            path = generate_report()
        st.success(f"新增 {result['inserted']} 条；错误 {len(result['errors'])} 个；日报：{path.name}")
        if result["errors"]:
            st.warning("部分外部数据源失败，详情见 watchlist.log。其他数据已正常入库。")

since = now - timedelta(hours=hours)
all_rows = fetch_recent(
    cfg["database_path"],
    since.isoformat(timespec="seconds"),
    codes=selected_codes,
)
rows = filter_current_records(all_rows)
hidden_legacy_ir = len(all_rows) - len(rows)
df = pd.DataFrame(rows)

for col in [
    "event_time", "category", "subcategory", "source", "code", "name",
    "title", "summary", "url", "payload_json",
]:
    if col not in df:
        df[col] = ""

if df.empty:
    st.info(
        "资讯数据库里还没有该时间窗的数据；问董秘仍可在下方直接实时查询。"
    )
if hidden_legacy_ir:
    st.caption(
        f"已自动隐藏 {hidden_legacy_ir} 条旧交易所互动记录；"
        "问董秘只展示当前三市场分流渠道。"
    )

counts = df.groupby("category").size().to_dict()
c1, c2, c3, c4 = st.columns(4)

c1.metric(
    "资讯",
    counts.get("news", 0),
)

c2.metric(
    "公告",
    counts.get("announcement", 0),
)

c3.metric(
    "大宗交易",
    counts.get("block_trade", 0),
)

c4.metric(
    "问董秘",
    len(df[df["subcategory"].isin(CURRENT_IR_SUBCATEGORIES)]),
)
summary = (
    df.groupby(["code", "name", "category"]).size().unstack(fill_value=0).reset_index()
)
st.subheader("自选股概览")
st.dataframe(summary, width="stretch", hide_index=True)

tab3, tab1, tab2 = st.tabs(
    [
        "问董秘",
        "资讯 & 公告",
        "大宗交易",
    ]
)

with tab1:
    x = df[df["category"].isin(["news", "announcement"])].copy()
    st.dataframe(
        x[["event_time", "name", "code", "category", "title", "source", "url"]],
        width="stretch",
        hide_index=True,
        column_config={"url": st.column_config.LinkColumn("原文")},
    )

with tab2:
    x = df[
        df["category"] == "block_trade"
    ].copy()
    if x.empty:
        st.info("该时间窗口没有大宗交易。")
    else:
        details = block_trade_details(x)
        st.subheader(f"大宗交易逐笔明细（共 {len(details)} 单）")
        st.caption("数据优先来自东方财富数据中心；每一行就是一单大宗交易。")
        st.dataframe(
            details,
            width="stretch",
            hide_index=True,
            column_config={
                "成交额（万元）": st.column_config.NumberColumn(format="%.2f"),
                "成交价（元）": st.column_config.NumberColumn(format="%.2f"),
                "成交量（万股）": st.column_config.NumberColumn(format="%.2f"),
                "折溢率（%）": st.column_config.NumberColumn(format="%.2f%%"),
                "原文": st.column_config.LinkColumn("东方财富明细页"),
            },
        )

with tab3:
    st.success(
        "已按市场自动分流：深圳股票→深交所互动易；上海主板/科创板→"
        "上证 e 互动；北交所股票→全景网北交所互动专区。"
    )
    st.caption(
        "三个平台的栏目结构并不完全相同；本页统一列出已获得公司回复的“最新答复”。"
    )
    query_col, button_col = st.columns([4, 1])
    with query_col:
        ir_code_input = st.text_input(
            "股票代码",
            placeholder="输入任意 6 位 A 股代码，例如 603986",
            key="ir_code_input",
        )
    with button_col:
        st.write("")
        st.write("")
        query_submitted = st.button("查询问董秘", width="stretch")

    if query_submitted:
        try:
            st.session_state["ir_query_code"] = normalize_stock_code(ir_code_input)
        except ValueError as exc:
            st.error(str(exc))

    active_ir_code = st.session_state.get("ir_query_code")
    live_rows = []
    active_platform = None
    if active_ir_code:
        active_platform = platform_for_code(active_ir_code)
        try:
            with st.spinner(
                f"正在从{active_platform['name']}查询 {active_ir_code}…"
            ):
                live_rows = load_exchange_ir_live(
                    active_ir_code,
                    cfg["timezone"],
                )
            st.markdown(
                f"当前股票：**{active_ir_code}**　"
                f"自动选择：**{active_platform['name']}**　"
                f"[打开平台原页]({active_platform['url']})"
            )
        except Exception as exc:
            st.error(f"{active_platform['name']}查询失败：{exc}")

    live_df = pd.DataFrame(live_rows)
    for col in [
        "event_time", "subcategory", "source", "code", "name", "title",
        "summary", "url",
    ]:
        if col not in live_df:
            live_df[col] = ""

    section_df = live_df[live_df["subcategory"] == "latest_reply"]
    st.subheader("最新答复")
    if section_df.empty:
        if active_ir_code:
            st.info(f"{active_platform['name']}目前没有返回该股票的最新答复。")
        else:
            st.info("输入股票代码并点击“查询问董秘”。")
    else:
        st.caption(f"共列出 {active_platform['name']} 最新 {len(section_df)} 条")
        st.dataframe(
            section_df[
                ["event_time", "name", "code", "title", "summary", "source", "url"]
            ],
            width="stretch",
            hide_index=True,
            column_config={
                "event_time": "答复时间",
                "name": "股票",
                "code": "代码",
                "title": "投资者问题",
                "summary": "公司答复",
                "source": st.column_config.TextColumn("抓取平台"),
                "url": st.column_config.LinkColumn("平台原文"),
            },
        )
