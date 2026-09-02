# 待解决事项 (TODO)

## 回测驱动的体系改进

### P1 · 1D 三买的次级别移动止盈（待 A/B 实验）
- **背景**: sanmai_hold_test.py 显示「持有至一卖」在 ≤4H 上大幅改善（4H 复利 +260%→+713%，
  1H +61%→+676%），但 1D 反而变差（+4.68%→-0.12% 均笔）：逐笔配对显示 20-10-05 三买
  base +229% → hold +98%，日线级别中途回撤 (-30% 级) 吞掉浮盈。
- **假设**: 大级别持仓、次级别离场 —— 1D 三买入场后用 4H 的一卖/二卖做移动止盈，
  既避免被中途回撤洗出（优于遇任何 1D 卖点即走），又不至于死扛到 1D 一卖回吐一半。
- **做法**: run_backtest 变体 —— 1D 三买入场后，平仓事件改为 4H 一卖/二卖的 confirmedT
  映射到 1D 时间轴；止损仍用 1D 买点最低价。
- **状态**: 未开始

### P2 · a_mode 默认口径切换
- fixA-enter（进入段基准，原文口径）已在三个周期验证显著优于 strongest（链内最强段），
  建议 chanlun.py 默认 a_mode="enter"，并重新生成 data.js。
- 注意: 会改变前端展示的全部一买/一卖信号，需同步核对 divergence_viewer。

### P3 · link_levels 标签语义修正
- multilevel_backtest.py 证明「次级别一买 ✓/✗」标签只对大级别**一买**有意义；
  二买/三买挂此标签是语义错配（联动率仅 5%/1%）。应改为仅对一买标注。

### P4 · 补实现盘整背驰信号
- 二买在次级别上的真实对应物是盘整背驰（原文第25课），系统未实现导致
  「4H二买 ∧ 1H盘整背驰」联动无法验证。实现后重跑 multilevel_backtest。

### P5 · CONFIRM_WINDOW 按级别差异化
- CONFIRM_WINDOW=3 在 1D 代价小，在 1H 最多延迟 3 根K线；
  1H 笔级止损距离中位仅 1.0%、67% 被打止损与此有关。试验 1H 用 window=1~2。

## 教学界面 (chanlun_tutorial)
- [x] 案例数据生成 build_tutorial_data.py → tutorial_data.js
- [x] tutorial.html 十章教学 + lightweight-charts 案例
