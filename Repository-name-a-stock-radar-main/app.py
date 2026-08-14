from datetime import timedelta
import json

import pandas as pd
import streamlit as st

from collectors.eastmoney_ir import query_eastmoney_ir
from config import load_config
from db import fetch_recent
from pipeline import run_once
from report import generate_report
from stocks import (
    Stock,
    add_watchlist_codes,
    get_watchlist,
    infer_market,
    normalize_stock_code,
    parse_stock_codes,
    remove_watchlist_codes,
    resolve_stock_name,
)
from utils import now_tz

APP_VERSION = "V4 · 东方财富直连版"


@st.cache_data(ttl=300, show_spinner=False)
def load_eastmoney_ir_live(code: str, timezone: str):
    return query_eastmoney_ir(code, tz_name=timezone, pages=1)


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
    "问董秘直接实时查询东方财富"
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

    if st.button("立即刷新数据", type="primary", width="stretch"):
        with st.spinner("正在抓取公开数据源…"):
            result = run_once(watchlist)
            path = generate_report()
        st.success(f"新增 {result['inserted']} 条；错误 {len(result['errors'])} 个；日报：{path.name}")
        if result["errors"]:
            st.warning("部分外部数据源失败，详情见 watchlist.log。其他数据已正常入库。")

since = now - timedelta(hours=hours)
rows = fetch_recent(cfg["database_path"], since.isoformat(timespec="seconds"), codes=selected_codes)
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

c4.metric("问董秘", len(df[df["subcategory"].isin([
    "latest_reply", "rumor_verification", "company_release",
])]))
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
        "抓取渠道：东方财富网－问董秘。这里不调用深交所互动易或上证 e 互动接口。"
    )
    st.caption(
        "部分深圳公司内容在东方财富原页会注明“来自深交所互动易”；"
        "这是东方财富标出的原始发布平台，不是本 App 的抓取渠道。"
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
        query_submitted = st.button("查询东方财富", width="stretch")

    if query_submitted:
        try:
            st.session_state["ir_query_code"] = normalize_stock_code(ir_code_input)
        except ValueError as exc:
            st.error(str(exc))

    active_ir_code = st.session_state.get("ir_query_code")
    live_rows = []
    if active_ir_code:
        try:
            with st.spinner(
                f"正在从东方财富查询 {active_ir_code} 的三个问董秘分项…"
            ):
                live_rows = load_eastmoney_ir_live(
                    active_ir_code,
                    cfg["timezone"],
                )
            st.markdown(
                f"当前股票：**{active_ir_code}**　"
                f"[打开东方财富问董秘原页]"
                f"(https://guba.eastmoney.com/qa/search?code={active_ir_code}&type=1)"
            )
        except Exception as exc:
            st.error(f"东方财富查询失败：{exc}")

    live_df = pd.DataFrame(live_rows)
    for col in [
        "event_time", "subcategory", "source", "code", "name", "title",
        "summary", "url",
    ]:
        if col not in live_df:
            live_df[col] = ""

    ir_tabs = st.tabs(["最新答复", "传闻求证", "公司发布"])
    ir_sections = ["latest_reply", "rumor_verification", "company_release"]
    for ir_tab, section in zip(ir_tabs, ir_sections):
        with ir_tab:
            section_df = live_df[live_df["subcategory"] == section]
            if section_df.empty:
                if active_ir_code:
                    st.info("东方财富该分项暂无资料。")
                else:
                    st.info("输入股票代码并点击“查询东方财富”。")
            else:
                st.caption(f"共列出东方财富最新 {len(section_df)} 条")
                st.dataframe(
                    section_df[
                        ["event_time", "name", "code", "title", "summary", "source", "url"]
                    ],
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "event_time": "时间",
                        "name": "股票",
                        "code": "代码",
                        "title": "问题 / 标题",
                        "summary": "公司答复 / 摘要",
                        "source": st.column_config.TextColumn("抓取渠道"),
                        "url": st.column_config.LinkColumn("东方财富原文"),
                    },
                )
