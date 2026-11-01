# labs —— 想法 / 实验 / 回测的收口区

> 原则（REPO_AUDIT §5.2「实验三态」）：任何想法只有三种归宿，**没有结论的实验不许留在根目录**。
>
> `labs/ideas`（一页纸假设）→ `labs/experiments`（可复跑，登记在 `registry.yaml`）→ `apps/platform-*`（产品化）

## 目录

| 目录 | 放什么 | 形态 |
| --- | --- | --- |
| `labs/ideas/` | 还没验证的想法、待验证假设、TODO 清单 | Markdown，一页一条 |
| `labs/experiments/` | 可复跑的实验与登记表 | `registry.yaml` + 指向脚本/产物的路径 |
| `labs/backtests/` | 历史回测脚本的统一入口 | 薄封装，委托 `scripts/backtests/*`（保留可回滚） |

## 登记表

`labs/experiments/registry.yaml` 是唯一事实源，字段：

```yaml
- id: ma-a-atr-entry            # 唯一 id（也是产物目录名）
  title: MA A 策略实体过滤对比
  hypothesis: 用 ATR14 实体过滤替代固定 3% 能提高期望
  status: active                # active | promoted | archived
  data: [US equity, HK equity, CN equity, gold, crypto]
  command: python3 experiments/ma_a_atr_entry/run_experiment.py
  artifacts: experiments/ma_a_atr_entry/results
  conclusion_date: null         # 有结论时填日期，并写一行结论
```

新增实验的流程：

1. 在 `labs/ideas/` 写一页纸（假设 / 判据 / 数据 / 成本）；
2. 建 `experiments/<id>/`（脚本 + README），在 `registry.yaml` 登记；
3. 跑出结论后把 `status` 改成 `promoted`（进产品）或 `archived`（写结论、冻结）；
4. `pytest apps/platform-api/tests/test_labs_registry.py` 会检查登记表与目录是否一致。

## 回测入口

```bash
# MA 同族（base=A/B 突破+回调 / 3x=杠杆 / c=策略 C）统一入口
python3 labs/backtests/ma.py --variant base --symbol BTC-USD --timeframe 1day --no-excel --no-html
python3 labs/backtests/ma.py --variant 3x --symbol BTC-USD --leverage 3 --no-excel
python3 labs/backtests/ma.py --variant c --symbol BTC-USD --no-excel

# 查看全部实验状态
python3 labs/backtests/ma.py --list
```
