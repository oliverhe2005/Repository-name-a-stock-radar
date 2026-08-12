from datetime import timedelta

import pandas as pd
import streamlit as st

from config import load_config
from db import fetch_recent
from pipeline import run_once
from report import generate_report
from stocks import WATCHLIST
from utils import now_tz

st.set_page_config(page_title="A股自选股资讯雷达", layout="wide")
cfg = load_config()
now = now_tz(cfg["timezone"])

st.title("A股自选股资讯雷达")
st.caption("资讯 / 公告 / 大单成交 / 东方财富资金流 / 问董秘，默认查看最近 24 小时")

with st.sidebar:
    hours = st.selectbox("时间窗口", [6, 12, 24, 48, 72], index=2)
    stock_options = {f"{s.name} ({s.code})": s.code for s in WATCHLIST}
    selected_labels = st.multiselect("股票", list(stock_options), default=list(stock_options))
    selected_codes = [stock_options[x] for x in selected_labels]
    if st.button("立即刷新数据", type="primary", use_container_width=True):
        with st.spinner("正在抓取公开数据源…"):
            result = run_once()
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

for col in ["event_time", "category", "source", "code", "name", "title", "summary", "url"]:
    if col not in df:
        df[col] = ""

counts = df.groupby("category").size().to_dict()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("资讯", counts.get("news", 0))
c2.metric("公告", counts.get("announcement", 0))
c3.metric("大单成交", counts.get("big_trade", 0))
c4.metric("资金流", counts.get("fund_flow", 0))
c5.metric("问董秘", counts.get("ir", 0))

summary = (
    df.groupby(["code", "name", "category"]).size().unstack(fill_value=0).reset_index()
)
st.subheader("自选股概览")
st.dataframe(summary, use_container_width=True, hide_index=True)

tab1, tab2, tab3 = st.tabs(["资讯 & 公告", "大单 & 资金流", "问董秘"])

with tab1:
    x = df[df["category"].isin(["news", "announcement"])].copy()
    st.dataframe(
        x[["event_time", "name", "code", "category", "title", "source", "url"]],
        use_container_width=True,
        hide_index=True,
        column_config={"url": st.column_config.LinkColumn("原文")},
    )

with tab2:
    x = df[df["category"].isin(["big_trade", "fund_flow"])].copy()
    st.dataframe(
        x[["event_time", "name", "code", "category", "title", "summary", "source", "url"]],
        use_container_width=True,
        hide_index=True,
        column_config={"url": st.column_config.LinkColumn("来源")},
    )

with tab3:
    x = df[df["category"] == "ir"].copy()
    st.dataframe(
        x[["event_time", "name", "code", "title", "summary", "source", "url"]],
        use_container_width=True,
        hide_index=True,
        column_config={"url": st.column_config.LinkColumn("互动平台")},
    )
