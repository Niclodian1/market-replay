#!/bin/bash
# 一键更新: 拉取最新行情(Binance 增量) → 重建 BTC/ETH 缠论数据文件
#
# 用法:
#   ./update_all.sh                全流程(行情拉取 + 双品种重建, 约 16~17 分钟)
#   ./update_all.sh --skip-fetch   跳过行情拉取, 仅用现有 parquet 重建数据文件
#
# 说明:
#   [1] update_data.py   — Binance 现货公开K线增量补齐 BTC/ETH 的 1D/4H/1H parquet
#   [2] chanlun.py BTC   — 重建 data.js      (缠论全链: 分型/笔/线段/中枢/买卖点)
#   [3] chanlun.py ETH   — 重建 data_eth.js
set -e
cd "$(dirname "$0")"

if [ "$1" != "--skip-fetch" ]; then
  echo "== [1/3] 更新行情数据(Binance 增量) =="
  python3 update_data.py
else
  echo "== [1/3] 跳过行情拉取(--skip-fetch) =="
fi

echo "== [2/3] 重建 BTC 缠论结构 (data.js) =="
python3 ../fenxing/chanlun.py BTC

echo "== [3/3] 重建 ETH 缠论结构 (data_eth.js) =="
python3 ../fenxing/chanlun.py ETH

echo ""
echo "== 完成 ✓ =="
ls -lh ../fenxing/data-view/data.js ../fenxing/data-view/data_eth.js
