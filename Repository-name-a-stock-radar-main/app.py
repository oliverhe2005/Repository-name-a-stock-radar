from datetime import timedelta

import pandas as pd
import streamlit as st

from config import load_config
from db import fetch_recent
from pipeline import run_once
from report import generate_report
from stocks import add_watchlist_codes, get_watchlist
from utils import now_tz

st.set_page_config(page_title="A股自选股资讯雷达", layout="wide")
cfg = load_config()
now = now_tz(cfg["timezone"])

st.title("A股自选股资讯雷达")
st.caption("资讯 / 公告 / 大宗交易 / 东方财富问董秘，默认查看最近 24 小时")
watchlist = get_watchlist()
with st.sidebar:
    hours = st.selectbox("时间窗口", [6, 12, 24, 48, 72], index=2)
    stock_options = {f"{s.name} ({s.code})": s.code for s in watchlist}
    selected_labels = st.multiselect("股票", list(stock_options), default=list(stock_options))
    selected_codes = [stock_options[x] for x in selected_labels]

    with st.form("add_watchlist_form", clear_on_submit=True):
        new_codes = st.text_area(
            "增加自选股票代码",
            placeholder="例如：600519, 000858\n可用逗号、空格或换行分隔",
            height=90,
        )
        add_submitted = st.form_submit_button(
            "添加到自选股",
            width="stretch",
        )
    if add_submitted:
        try:
            with st.spinner("正在校验股票代码…"):
                added = add_watchlist_codes(new_codes)
            if added:
                st.session_state["watchlist_flash"] = "已添加：" + "、".join(
                    f"{stock.name} ({stock.code})" for stock in added
                )
            else:
                st.session_state["watchlist_flash"] = "这些代码已在自选股中。"
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

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

if df.empty:
    st.info("数据库里还没有该时间窗的数据。先点击左侧“立即刷新数据”，或运行 python daily_job.py。")
    st.stop()

for col in [
    "event_time", "category", "subcategory", "source", "code", "name",
    "title", "summary", "url",
]:
    if col not in df:
        df[col] = ""

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

tab1, tab2, tab3 = st.tabs(
    [
        "资讯 & 公告",
        "大宗交易",
        "问董秘",
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
    st.dataframe(
        x[["event_time", "name", "code", "title", "summary", "source", "url"]],
        width="stretch",
        hide_index=True,
        column_config={"url": st.column_config.LinkColumn("东方财富")},
    )

with tab3:
    x = df[df["category"] == "ir"].copy()
    ir_tabs = st.tabs(["最新答复", "传闻求证", "公司发布"])
    ir_sections = ["latest_reply", "rumor_verification", "company_release"]
    for ir_tab, section in zip(ir_tabs, ir_sections):
        with ir_tab:
            section_df = x[x["subcategory"] == section]
            if section_df.empty:
                st.info("该时间窗口暂无数据。")
            else:
                st.dataframe(
                    section_df[
                        ["event_time", "name", "code", "title", "summary", "source", "url"]
                    ],
                    width="stretch",
                    hide_index=True,
                    column_config={"url": st.column_config.LinkColumn("东方财富")},
                )
