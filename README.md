# A股自选股资讯雷达

一个面向自选股的每日自动扫描平台，默认覆盖最近 24 小时：

1. **资讯**：东方财富个股新闻
2. **公告**：东方财富沪深京 A 股公告聚合
3. **大单**：
   - 东方财富个股资金流中的主力 / 超大单 / 大单净流入
   - 沪深股票若公开逐笔成交接口可用，则额外筛选单笔成交额 ≥ 100 万元的成交
4. **问董秘 / 投资者互动**：
   - 深交所：互动易
   - 上交所：上证 e 互动
   - 北交所：由于没有与前两者完全对等、稳定的公开 Q&A API，默认使用同花顺 i问董秘页面作 best-effort fallback；失败不会影响其他模块
5. **持久化**：SQLite + 去重
6. **输出**：Streamlit 仪表盘 + Markdown 日报
7. **自动化**：工作日 17:10（北京时间）运行，可在 `config.json` 修改

## 1. 安装

建议 Python 3.11–3.13：

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

## 2. 首次抓取

```bash
python daily_job.py
```

数据写入 `data/watchlist.db`，日报写入 `reports/YYYY-MM-DD.md`。

## 3. 启动仪表盘

```bash
streamlit run app.py
```

仪表盘左侧可手动立即刷新。

## 4. 自动运行

### 方法 A：保持 scheduler 常驻

```bash
python scheduler.py
```

默认北京时间周一至周五 17:10 执行。

### 方法 B：系统 cron（更稳）

```cron
10 17 * * 1-5 cd /ABSOLUTE/PATH/a_share_watchlist_monitor && /ABSOLUTE/PATH/.venv/bin/python daily_job.py >> cron.log 2>&1
```

服务器时区不是 Asia/Shanghai 时，请按服务器时区换算或设置 `CRON_TZ=Asia/Shanghai`（取决于系统 cron 支持）。

## 5. 大单口径

默认 `big_trade_threshold_cny = 1000000`（100 万元）。修改 `config.json` 即可。

要区分两类数据：

- `big_trade`：可获得逐笔数据时，筛选实际成交明细；沪深为主。
- `fund_flow`：东方财富计算的“大单/超大单/主力净流入”，是资金流统计口径，不等价于交易所逐笔大单。

东方财富 Level-2 的逐笔还原属于权限型服务，本项目**不会绕过登录、订阅或反爬限制**。如果你有合法 Level-2 / Choice / Wind / iFinD / 聚宽等数据权限，可以新增 adapter 替换公开数据源。

## 6. 北交所问董秘说明

深交所有公开互动易，上交所有公开上证 e 互动；北交所目前没有在本项目中确认到完全对等、稳定、无需认证的官方公司问答 API。因此 `920045 蘅东光` 默认走公开的同花顺 i问董秘页面做容错采集。如果你要求 **official-only**，建议关闭：

```json
"enable_bse_ir_fallback": false
```

此时北交所只保留公告、资讯和资金流，问答项为空，避免混入第三方数据。

## 7. 当前股票池

- 北方华创 002371
- 松发股份 603268
- 柏楚电子 688188
- 通富微电 002156
- 绿的谐波 688017
- 光库科技 300620
- 长鑫科技 688825
- 扬杰科技 300373
- 方邦股份 688020
- 沪电股份 002463
- 麦格米特 002851
- 蘅东光 920045
- 天孚通信 300394
- 东山精密 002384
- 新易盛 300502
- 宁德时代 300750
- 豪恩汽电 301488
- 兆易创新 603986

修改股票池只需要编辑 `stocks.py`。

## 8. 建议的下一步

生产环境建议再加：企业微信/Telegram/邮件推送、关键词重要性打分、LLM 摘要、公告类型优先级、资金流异常阈值、Docker 化和云端部署。
