# chanlun-web（缠论练习/教学静态站）

纯静态站点：`index.html` + `js/` + `css/` + `vendor/`，缠论计算在后端
（`packages/chanlun-core` 引擎 / `apps/platform-api`），本站只读数据产物。

## 运行

```bash
# 仓库根：起一个静态服务器
python3 -m http.server 8081 --directory apps/chanlun-web
# 打开 http://localhost:8081/index.html
```

## 数据产物（不入库）

`data-view/*.js`（缠论全链结果）与 `intraday/**/*.bin`（月度分片）是**构建产物**，
已从 git 移除并写入 `.gitignore`（历史里仍有，随时可 `git checkout <commit> -- <path>` 取回）。

重建方式：

```bash
# 1) 行情 parquet 增量更新（data/crypto、data/stocks…）
python3 scripts/ops/update_data.py

# 2) 重建 data-view/*.js（BTC/ETH 缠论全链）
python3 apps/chanlun-web/chanlun.py BTC
python3 apps/chanlun-web/chanlun.py ETH

# 3) 重建分时月度分片（intraday/**/*.bin + manifest）
python3 scripts/ops/build_intraday_chunks.py

# 4) 其他品种/教学数据
python3 apps/chanlun-web/tools/build_mobile_data.py
python3 apps/chanlun-web/tools/build_trades_data.py
python3 apps/chanlun-web/tools/build_tutorial_data.py
```

> 一键脚本：`scripts/ops/update_all.sh`（拉行情 + 重建 BTC/ETH 的 data-view）。

## 测试

```bash
node --check apps/chanlun-web/js/*.js          # CI 已跑
node --test apps/chanlun-web/tests/*.test.mjs   # 参数实验室引擎(19)
pytest packages/chanlun-core/tests -q          # 引擎回归
```

### 均线斜率高亮

工具栏 `📐 斜率`：每条均线一行滑块，斜率（%/根）落在区间内的均线段在图上加粗+发光，
拖滑块实时变。条件是「斜率 < X / > X / |斜率| < X / |斜率| > X」，窗口 1~10 根。
默认关闭，点按钮才生效。实现在 `js/10-ma-slope.js`，斜率口径与验证见
`docs/refactor/IMPLEMENTATION_2026-10-22.md` §38。

### 日线 MA 突破 × 斜率（`📈 趋势`）

持仓 = 收盘 > MA(p) 且 MA 的 w 根斜率 > 阈值；图上蓝色底色标持仓区间、▲ 入场、▼ 出场，
面板里拖均线/窗口/阈值三个滑块实时重算，并统计"19 段 ≥30% 牛市里覆盖了几段（捕获 ≥50%）"。
默认 MA50 / 5 根 / −0.15 %/根：95.8x、回撤 −55%、持仓 47%，15/19 段覆盖了全部牛市涨幅的 92%。
研究与局限见 `reports/ma-slope/研究-日线MA突破与斜率.md`，实现在 `js/11-ma-trend.js`。

### MA20/60 双均线（`📊 双均线`）

金叉买 / 死叉卖，信号 K 线画框并闪烁 6 秒（可勾选常闪、可重播）；面板实时给胜率、盈亏比、
平均/中位收益、平均持仓、复利。32 笔 / 胜率 47% / 盈亏比 4.47 / 均值 +19.7% —— 典型趋势跟随，
5 笔大赢占全部正收益 86%，去掉就是亏的。金叉当根是大阳线（实体 >4%）反而别追。
研究见 `reports/ma-slope/研究-双均线MA20-60.md`，实现在 `js/12-ma-dual.js`。

### 标注模式（`🏷 标注`，独立右侧抽屉）

规则先出信号，人再逐个复判"这单我会不会做"：11 个实验源（单均线突破 MA20/60/120、双均线金叉 20×60/60×120/20×120、
趋势、突破+斜率、缠论买点·笔/线段、背驰买点），←/→ 逐笔过，1 会做 / 2 不做 / 3 跳过，
可选 12 个理由标签 + 备注。默认遮住信号之后的走势（盲判，主图+量能+MACD 一起遮，并用覆盖层盖住右侧价格轴），未来 5/20/60 日收益折叠起来先判断再看。
判定存 localStorage（按 品种|周期|实验 隔离），可导出 JSON 用于统计"你的判断 vs 规则口径"。
实现在 `js/13-annotate.js`。

### 副图（MACD / 量能）开关

关掉的副图是"移到视口外"而不是 `display:none`：容器一旦变成 0×0，lightweight-charts
的时间轴会退化并把渲染线程跑满（页面卡死 / 重新打开空白）。改样式时不要改回
`display:none`。详见 `docs/refactor/IMPLEMENTATION_2026-10-22.md` §37。

### 参数实验室与品种绑定

实验室结果是前端按当前 K 线重算的，切品种（或周期）后会重建；面板标题带品种名
（`参数实验室 · ETH`）。注意实验室的买点口径与生产数据不同，见
`docs/refactor/IMPLEMENTATION_2026-10-22.md` §36。

## 笔的区别（图解页）

`bi-compare.html`：同一段 K 线，用「原文 / 图4 / 引擎」三套口径各画一遍笔，红笔标出差异，
配 图3/图4/图5 示意图。第 5 个 tab「买点」把一买/二买/三买按口径分色画在图上（▲◆■，蓝=只在原文、
橙=只在引擎、白=共有），右侧列出 22 条差异可点击跳转。主图下方带 MACD(12,26,9) 副图（DIF/DEA/柱），
一买的背驰和绿柱缩短直接对着看。入口在参数实验室面板标题栏「笔的区别?」。

## 收起右侧面板（只留图表）

「复盘交易」卡标题右上「⇥」= 收起整个右栏（只留主图 + 量 + MACD，图表占满全宽）；
收起后图表右上角出现「« 复盘交易」一键展开，顶部「⇱/⇲ 侧栏」是同一个开关，状态记在
`localStorage`（刷新保持）。

## 参数实验室（🎛 顶部按钮）

纯前端重算，不改 `data-view/*.js`：滑动条调 MACD / 背驰比 / 一买口径 / 成笔间隔 /
中枢重叠 / 中枢级别，主图实时重画 中枢·笔·背驰·一/二/三买。

- 一买 = **点位**：底背驰后「绿柱首次开始缩短」那根 K 线的**收盘价**（口径可切
  「不再缩短 / 柱体翻红」对比）；二买/三买点同时给出结构区间带。
- 复盘态自动按 `replayT` 截断，无未来信息。
- 默认参数 = 引擎口径：分型确认 3（收盘确认窗口）、中枢重叠 0（不过滤），因此默认下**笔/笔中枢与原生图层逐笔一致**；滑条调松即可看原文纯几何口径（1D 笔 195 → 235）。
- 实现：`js/09-lab-engine.js`（纯计算）+ `js/09-params-lab.js`（面板/图层），
  口径与验收见 `docs/refactor/IMPLEMENTATION_2026-10-22.md` §30。

## MA20 简化 v2 校核

顶部「标注 → 我的规则 → MA20 简化 v2 · 阳线上穿候选」。条件仅为前收盘≤前MA20、本收盘>本MA20且本根阳线；26条排除规则已退出页面。当前日线快照 BTC 181 条、ETH 170 条，0/1/2 项确认均保留。`index.html?cover67=ETH` 兼容原链接，进入全部上穿查漏模式。

- 实体≥0.5ATR、量比≥1.2各计一项确认，只分级不过滤；量能颜色仍沿用高/普通/低三色。图上卡片可看相同确认项数量和相同量能档位的参考表现。
- 新源 `ma20_simple_v2` / `ma20_simple_check_v2` 独立保存新判断，旧版标签不自动带入；历史记录仍保留并随导出按 source 区分。
- 新增结构化复盘表单：区域、触发、行情类型、把握、主要依据、调整幅度/时间/收敛、结构参照状态为会做/不做前必填项，支持不确定/不适用，跳过免填全。可填写调整起止、参照高点、箱体及此前更好买点；日期旁「图上选择」可点击主图K线回填并高亮，Esc取消，合并视角自动切原始；内置虚构示例。保存、导入、导出保留 assessment 原始字段及 assessmentEvidence 时间价格与派生结构指标。量化参考展示力度、回撤、时间、收缩和震荡代理，不自动据此填写判断。
- 默认遮住未来，统计只纳入当前信号之前已经退出的交易。参考口径：次根开盘进场、信号低点止损、跌破MA20次根开盘退出、单边成本0.10%。
- 说明与标注建议：`reports/ma20-simplification-2026-10-30/新一轮标注说明.md`。

当前计算验证：`node apps/chanlun-web/tools/verify_ma20_simple.mjs`。浏览器回归：独立静态服务8878端口、独立Chrome CDP9337端口后，运行 `node apps/chanlun-web/tools/verify_ma20_simple_browser.mjs`。旧版只保留 `13-cover67-legacy.js` 研究文件，`verify_cover67.mjs` 验证存档70/54个信号，不是当前前端信号。

### 普通图表上的指标栏

“视角”右侧的“指标 → MA突破”独立控制全图买点，开关状态会保存。点“买点列表”可按日期查看当前品种/周期的全部算法买点，点击条目或使用上一条/下一条定位；“标注校核此买点”衔接已有标注模式。切换品种和周期会重算列表，合并 K 线上同一根内的多个买点合并显示数量。标注遮住未来时，指标和列表也只显示当前标注日期及以前的信号。

界面回归：`node apps/chanlun-web/tools/verify_cover67_indicator_browser.mjs`（同上独立服务/CDP端口）。

### 买点量能卡片

开启“指标 → MA突破”后，点击图上的算法买点箭头或对应 K 线，可在图表上弹出量能卡片；买点列表点击条目也可打开。高量能为蓝色（量比≥1.2），普通量能为橙色（0.9≤量比<1.2），低量能为紫色（量比<0.9），缺失量能为灰色；选中时保留量能颜色。合并K线包含不同档位时显示灰色汇总，点击后可分别查看原始买点。

量比=信号本根成交量÷此前20根完整K线的平均成交量，分母不含信号本根，按当前周期计算；1.20表示比此前均量多20%。卡片中可展开当前品种同档位的历史表现，盲判时仅使用当前标注点之前已退出的交易。算法标注面板移除量能数字及分档历史，改为“图上查看量能”入口。

浏览器回归：`node apps/chanlun-web/tools/verify_cover67_volume_browser.mjs`，覆盖真实图表点击、三档颜色、公式说明、品种切换关闭旧卡片和盲判统计。

### 标注：同根双均线突破与 MACD 创新低背离

“标注 → 双均线突破”新增 MA20＋MA60、MA20＋MA120、MA60＋MA120 三个组合：本根必须为阳线，开盘≤两条当根SMA中的较低值，收盘>两条中的较高值。仅下影线触及不算；这是阳线实体同时穿越，与原有“均线金叉”分别保存。

“标注 → MACD背离”提供“创新低＋DIF底背离候选”和“创新低＋MACD负柱背离候选”。默认当根最低价严格低于此前60根最低价，与参照低点至少间隔5根；参照点至少完成35根MACD预热，两处DIF或柱值都在零下且当前值高于前低值。MACD为EMA(12,26,9)，柱为DIF−DEA，不乘2。信号记录在创新低当根收盘，可继续下跌，不表示底部或反转已确认。

每条显示参照日期、前低/新低价格、指标比较和间隔；标注及导出保留 `signalEvidence`，各入口使用独立源ID，不覆盖原有标注。候选只使用当根和此前数据，不使用未来分型/笔确认。

验证：`node apps/chanlun-web/tools/verify_annotation_signals.mjs`；浏览器验证：`node apps/chanlun-web/tools/verify_annotation_signals_browser.mjs`（独立服务8878及CDP9337）。
