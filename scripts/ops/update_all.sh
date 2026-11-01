#!/bin/bash
# 一键更新全部品种、图表数据与15分钟分片；Windows 可直接运行同名 Python 入口。
#
# 用法:
#   ./update_all.sh                全部已有品种的行情拉取、图表与分时重建
#   ./update_all.sh --skip-fetch   跳过行情拉取, 仅用现有 parquet 重建数据文件
#
set -e
cd "$(dirname "$0")/../.."
python3 scripts/ops/update_all_data.py "$@"
