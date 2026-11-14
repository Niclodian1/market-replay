# btc-ai-poc — BTC AI 交易模块概念验证

完整报告：[REPORT.md](REPORT.md)

| 脚本 | 作用 | 耗时 |
| --- | --- | --- |
| `run_experiment.py` | v1：LightGBM + Triple-Barrier + Walk-Forward；纯 ML 与 Meta-Labeling 两条线 + B&H / 均线基准 | 主配置约 16 秒，`--suite` 约 3 分钟 |
| `iteration_v2.py` | v2：AI 用作风控/过滤层（波动率预测仓位缩放、趋势段 meta 过滤、多周期过滤） | 约 20 秒 |
| `verify_no_lookahead.py` | 防未来函数自检（截断不变性 / 标签隔离 / 阳性对照泄漏） | 约 15 秒 |

```bash
python3 experiments/btc_ai_poc/run_experiment.py --suite     # v1 主配置 + 7 组稳健性/对照
python3 experiments/btc_ai_poc/iteration_v2.py               # v2
python3 experiments/btc_ai_poc/verify_no_lookahead.py        # 自检，非 0 退出 = 失败

# 其它数据源
python3 experiments/btc_ai_poc/run_experiment.py --csv my_btc_4h.csv --tag mycsv   # timestamp,open,high,low,close,volume
python3 experiments/btc_ai_poc/run_experiment.py --source ccxt --timeframe 4h      # 需 pip install ccxt
```

数据：`data/crypto/BTCUSDT_{1h,4h,1d}.parquet`（Binance 现货，2017-08-17 → 2026-11-03）。
口径：样本外 2020-01-01 起，季度滚动重训，单边手续费 0.05% + 单边滑点 0.05%（往返 0.20%），只做多、不加杠杆。
产物：`results/<tag>/results.json`、`equity_drawdown_auc.png`；equity/trades CSV 可重新生成，已加入 gitignore。
