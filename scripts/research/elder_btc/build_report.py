#!/usr/bin/env python3
"""Build Chinese research report, offline Plotly dashboard and executable frozen candidate."""
from pathlib import Path
import html,json,hashlib,re
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from plotly.offline import get_plotlyjs

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'reports/elder-btc-1d-2026-11-12'
HERE=Path(__file__).resolve().parent
def f(x,d=2):return '—' if x is None else f'{x:.{d}f}'
def render_rules(text):
    def inline(x):
        x=html.escape(x);x=re.sub(r'`([^`]+)`',r'<code>\1</code>',x);return re.sub(r'\*\*([^*]+)\*\*',r'<b>\1</b>',x)
    parts=[];table=False
    for line in text.splitlines():
        if line.startswith('|'):
            if re.fullmatch(r'[| :\-]+',line):continue
            if not table:parts.append('<table>');table=True
            parts.append('<tr>'+''.join('<td>'+inline(c.strip())+'</td>' for c in line.strip('|').split('|'))+'</tr>')
        else:
            if table:parts.append('</table>');table=False
            if not line.strip():continue
            depth=len(line)-len(line.lstrip('#'))
            parts.append(f'<h{min(depth+1,4)}>'+inline(line.lstrip('# '))+f'</h{min(depth+1,4)}>' if depth else '<p>'+inline(line)+'</p>')
    if table:parts.append('</table>')
    return ''.join(parts)
def main():
    s=json.loads((OUT/'summary.json').read_text());allr=json.loads((OUT/'all_results.json').read_text());bench=json.loads((OUT/'benchmarks.json').read_text())
    selected=s['selection']['research_candidate'];m=s['models'][selected];test=m['test'];inf=s['inference'][selected]
    decision=dict(preselected=selected,preselected_label=m['label'],statistically_validated=False,live_recommendation='NO_LIVE_STRATEGY_VALIDATED',
        reasons=['No configuration has Holm-adjusted positive mean-return evidence and adequate trade count.',
                 'No configuration has Holm-adjusted positive BTC regression alpha evidence and adequate trade count.',
                 'The preselected candidate is cost-sensitive and its held-out Sharpe is near zero.'],
        research_candidate_preserved=True,no_test_based_reselection=True)
    (OUT/'deployment_decision.json').write_text(json.dumps(decision,ensure_ascii=False,indent=2))
    rows=[]
    for name in ['value_raw','value_force','value_impulse','value_full','false_break','false_break_div','impulse_direct','stoch_pullback','macd_cross','triple_weekly_reference']:
        q=s['models'][name]['test'];rows.append('|'+s['labels'][name]+'|'+str(q['trades'])+'|'+f(q['win_rate'])+'%|'+f(q['payoff'])+'|'+f(q['profit_factor'])+'|'+f(q['total_return'])+'%|'+f(q['cagr'])+'%|'+f(q['max_drawdown'])+'%|'+f(q['sharpe'])+'|')
    costs=[]
    for a in s['sensitivity']:
        if a['kind']=='slippage':q=a['metrics'];costs.append(f"|{a['value']*10000:g} bp|{f(q['total_return'])}%|{f(q['sharpe'])}|{f(q['profit_factor'])}|")
    rules=(OUT/'书中逻辑与量化规则.md').read_text()
    report=f'''# 《以交易为生》第2版 × BTC日线：量化与回测结果

分析日：2026-11-12。主结论：**本轮尚不能证明任何被测配置在BTC 1D上有稳定统计优势。** 有些配置历史盈利、有些亏损、有些样本过少，三者不能混为一谈。当前不将任何配置标为已经验证、可直接实盘的最优系统。

## 书籍、数据和成本

已解析本地EPUB的74个章节文件并核对核心策略、资金管理及阿氏评分图片。原书包含多套方法，不能把所有指标并用当作唯一系统；详细原文节号映射和40余项无歧义定义见《书中逻辑与量化规则.md》。数学化的阈值是本研究的明确代理，并非作者对每个细节都有唯一规定。

使用 `data/crypto/BTCUSDT_1d.parquet`，2017-08-17至2026-11-02，共3365根连续UTC日线，无重复、缺日和非法OHLC。另一份 `BTC_USD_1day.parquet` 有79条非UTC零点时间戳及两处非整日步长，未混用。只核验本地快照内部一致性，没有把它冒充刚刚联网校验的交易所数据。

用户确认：**现货只做多、不加杠杆；买卖每边手续费0.05%，每边滑点0.05%，往返约0.20%。** 起始本金100000，单笔目标风险为月初净值1%，最大名义仓位100%，现金利率0。止损跳空按开盘价并扣滑点；不能保证实际最大亏损正好等于预算。

## 研究设计与防止过拟合

- 开发：2018-03-05—2022-12-31，1763天；验证：2023-01-01—2024-12-31，731天；本轮留出：2025-01-01—2026-11-02，671天。
- 12个预定单周期候选、1个周线原框架参照、15个单条规则对照，共28配置。没有遍历大参数网格。15个对照与周线参照不参与主方案选择。
- 筛选要求开发≥20笔、验证≥8笔；两个区间PF>1、夏普>0；开发最大回撤不劣于−35%、验证不劣于−25%；2019—2022的4个独立年度子回测至少3年正收益。
- 评分为两个区间夏普较小值减0.05×附加入场条件数。代码、输入和参数哈希与选择结果写入 `selection.json`，然后才运行2025以后的回测，未因留出结果更换赢家。
- 日线收盘后生成次日订单；不在信号日收盘看完后又填作当天开盘买入。止损只按已收盘数据更新，次日生效。周线参照只用已经收完的周。
- 每个区间均独立从现金开始；在预定末尾扣成本强平，使所有交易均可评估。旧行情仅用于指标预热，不将开发末尾仓位带入验证；年度子回测同样独立重置。
- 4000次配对循环区块bootstrap，区块20日、固定随机种子20261112；同一抽样索引用于规则收益差。28配置均值检验做Holm校正；另对28配置的BTC回归alpha检验作Holm校正；16个事先写入选择文件的规则差异检验另作Holm校正。95%区间是重采样区间，依赖这些历史数据和区块假设，不是未来收益保证。
- BTC行情此前已在本项目其他研究出现。本次是**本轮按时间留出的验证**，不等于研究者从未见过的全新市场，更不等于未来实时模拟。

## 事前选择与事后结果

事前冻结候选是 **{m['label']}（{selected}）**。它是检验机械动力交易的控制方案，不能改称作者推荐的完整动力系统；作者强调动力首先是禁入监测器。

| 区间 | 交易数 | 净收益 | 年化 | 最大回撤 | 夏普 | 利润因子 |
|---|---:|---:|---:|---:|---:|---:|
'''
    for seg,label in [('train','开发'),('validation','验证'),('test','留出')]:
        q=m[seg];report+=f"|{label}|{q['trades']}|{f(q['total_return'])}%|{f(q['cagr'])}%|{f(q['max_drawdown'])}%|{f(q['sharpe'])}|{f(q['profit_factor'])}|\n"
    report+=f'''
留出期净胜率 **{f(test['win_rate'])}%**，平均盈利金额/平均亏损金额 **{f(test['payoff'])}**，利润因子 **{f(test['profit_factor'])}**。胜率的分母是包括末尾强平的全部69笔完整交易，赢是扣成本后净利润>0；盈亏比不是利润因子，两者不要混用。

净值每日收盘估值；最大回撤是相对此前峰值的最大跌幅。年化收益为 `(末净值/初净值)^(365.25/天数)−1`。夏普是含空仓日、零无风险利率的日收益均值/样本标准差×√365.25。低仓位会减小账户回撤，不能把它直接理解成相对满仓BTC的更强择时能力。

留出日均净收益95%区间为 **[{inf['mean_daily_CI95'][0]*100:.4f}%, {inf['mean_daily_CI95'][1]*100:.4f}%]**，跨过0。均值检验原始p={inf['mean_p']:.3f}，28配置Holm校正后p={inf['mean_p_holm']:.3f}。BTC回归beta约{inf['beta']:.3f}，alpha区间也跨过0。**“已经证明稳定盈利”的结论不成立。**

## 同一留出区间的主要对照

| 固定实现 | 笔数 | 净胜率 | 盈亏比 | 利润因子PF | 净收益 | 年化 | 最大回撤 | 夏普 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
'''+ '\n'.join(rows)+'''

“回调＋动力禁入”留出33笔、净收益6.85%、PF1.72、夏普0.74，三个区间均为正收益，是更值得后续前瞻记录的方向；但它不是本轮事前选中的赢家，也没有通过校正后的显著性检验，**不能看完留出后把它改成已经验证的最优策略**。

随机指标回升方案虽有较好留出数字，但开发/验证不一致、留出只有12笔，也不能依据事后排名升级。假突破＋背离只有5笔；平缓通道1笔、袋鼠尾0笔、RSI0笔，属于证据不足，不能宣判这些原理普遍失效。

同区间买入持有净收益−18.07%、最大回撤−52.97%、夏普−0.04；仅EMA26方向＋相同风险管理对照净收益3.90%、回撤−4.81%、夏普0.35。满仓持有的暴露远高于低风险候选，不能只比较回撤就认定胜出。实际时点alpha也未获得统计支持。

## 保留、删除和暂不评价

1. **保留为研究框架**：只用当时可得数据、先定止损再算仓位、风险上限、止损不下移、成本和日志。这些主要是风险纪律，不等于已经证明有超额收益。单周期价值区＋动力禁入保持较简洁，但仍需新的前瞻数据。
2. **不加入当前简化候选**：FI2<0、放量≥1.25、ADX>20、不创强力新低、严格20～40根半深背离、额外阿氏高分等过滤。本轮没有足够证据证明增益；有的令交易数几乎归零。不能把“叠加更多确认”自动当作提高胜率。
3. **止损没有统一获胜版本**：次低点、安全区域与ATR初始止损替换未显示稳定改进。收盘1.5R移保本的附加版也未证明改善收益；它与前一轮盘中即时移保本的模型不同。
4. **不能称6%规则无效**：在value_full留出期它没有改变交易轨迹，所以无法从这批数据估计该规则收益增益。风控价值不必表现为提高夏普；是否触发和触发后影响必须分开。
5. **动力禁入有方向性线索，尚不显著**：在FI回调上增加禁入改善日收益，配对检验原始p约0.015，但16项Holm后约0.240，不足以认定统计优势。检验表把这一点保留下来，没有只报告显著的原始p值。
6. **不评价缺少数据的规则**：OI、市场宽度、COT、意见调查、新闻和个人情绪不能从BTC现货OHLCV凭空重建。本次不伪造数据也不声称检验了这些部分。

## 成本与参数稳定性

事前候选的手续费始终每边5bp，以下只改变每边滑点：

| 每边滑点 | 留出净收益 | 夏普 | PF |
|---|---:|---:|---:|
'''+ '\n'.join(costs)+'''

快速/慢速EMA长度共同缩短20%时，留出净收益约−1.92%；共同增长20%时约+1.03%。这是诊断，不用于重新选参。ATR跟踪倍数1.5/2/2.5在该候选留出交易上没有改变结果，因为颜色退出先触发；不能因此说倍数对所有市场都不重要。

事前候选在留出段没有日线同根成交冲突，不利与有利路径结果一致。其他配置的两种日内顺序情景另存 `execution_bounds.json`；它们是局部成交先后的诊断，不是整段路径全局收益的数学上下界，不会把当天先止损后的高点算作一定能获得的利润。突破入场和止盈是触价市价模型，按触价或开盘加不利滑点；不是忽略限价约束的假限价成交。

## 最终代码与复现

最终结论是 `NO_LIVE_STRATEGY_VALIDATED`。交付**完整可执行的事前冻结研究候选**，而不是事后挑一个留出最好的参数伪装成优化成功。`final_strategy.py` 内嵌冻结配置；它只运行离线回测，不发送真实交易订单。

```sh
python3 reports/elder-btc-1d-2026-11-12/final_strategy.py --data data/crypto/BTCUSDT_1d.parquet --start 2025-01-01 --end 2026-11-02 --out /tmp/elder-reproduce
```

完整实验可复现：

```sh
python3 -m unittest discover -s scripts/research/elder_btc -p test_strategy.py -v
python3 scripts/research/elder_btc/research.py --stage select
python3 scripts/research/elder_btc/research.py --stage test
python3 scripts/research/elder_btc/audit.py
python3 scripts/research/elder_btc/build_report.py
```

`selection.json`保存留出前的选择与哈希；`all_results.json`保留三段所有逐日净值和交易；`summary.json`保留指标、区块检验、16项规则对照、敏感性和年度结果；`书中逻辑与量化规则.md`保留原书依据与实现差异。页面筛选只切换显示，不改变冻结策略。

已通过13项执行/因果性测试，包括未来截断一致性、次日成交、双边成本、日内冲突、跳空、止损更新滞后、仓位不加杠杆、6%预算及跨月重置；另校验84个区间账户现金账、收益复利与信号/成交/退出顺序。执行审计补充修正了有利顺序情景中“触价买入后不可跳过必经止损”的处理，并禁止在已跌穿止损时建立新仓。审计前冻结文件和主结果保存在 `execution-audit-v1/`；逐项核对后，不利主回测的交易、净值、收益风险指标和事前选择均未改变，只有诊断冲突计数可变化，没有据留出表现调规则。审计记录为 `execution-audit.json`。

## 参考与证据边界

- 原书依据：用户提供的本地EPUB，重点§18、20、22—24、26—30、39—41、50—55、58—59；量化规则表明确区分新增默认值和原书说法。
- [作者官网工具说明](https://www.elder.com/product-page/elder-disk-for-metastock-v2-0)：确认三重滤网、动力、强力指数和安全区域等是不同组件。收益数据均来自本地代码，不来自该网页。
- [Bailey等《The Probability of Backtest Overfitting》](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)：反复比较配置会带来选择偏差；本次采用冻结、时间留出和多重检验，但没有声称消灭过拟合，也没有把简单留出说成论文中的完整CSCV/PBO估计。
- [TradingView官方成交模拟说明](https://www.tradingview.com/pine-script-docs/concepts/strategies/#broker-emulator)：OHLC本身不确定日内顺序。本次以不利顺序为主，并保存有利顺序诊断。

区块bootstrap和回归alpha也不能消除市场非平稳性、仅一个币种、历史较短、交易相关性及代理规则误差。下一步应冻结较简洁的候选做新的前瞻模拟，而不是继续在这671天里调到显著。
'''
    (OUT/'研究结论.md').write_text(report)
    # A single executable source file with embedded configuration, no relative module dependency.
    footer='''
if __name__=='__main__':
    import argparse,json
    from pathlib import Path
    p=argparse.ArgumentParser(description='Frozen research backtest; no live orders; not statistically validated')
    p.add_argument('--data',required=True);p.add_argument('--start',default='2025-01-01');p.add_argument('--end',default='2026-11-02');p.add_argument('--out',required=True)
    args=p.parse_args();raw=pd.read_parquet(args.data)
    if 'open_time' in raw:raw=raw.rename(columns={'open_time':'date'})
    elif 'time' in raw:raw['date']=pd.to_datetime(raw['time'],unit='s',utc=True)
    raw['date']=pd.to_datetime(raw.date,utc=True)
    assert raw.date.is_monotonic_increasing and not raw.date.duplicated().any()
    assert (raw.date.diff().dropna()==pd.Timedelta(days=1)).all(), 'Use continuous UTC daily data'
    cfg=Config(**FROZEN_CONFIG);d=indicators(raw,cfg);r=backtest(d,cfg,args.start,args.end);m=metrics(r)
    dest=Path(args.out);dest.mkdir(parents=True,exist_ok=True)
    convert=lambda x:x.item() if isinstance(x,np.generic) else str(x)
    (dest/'result.json').write_text(json.dumps({'status':'RESEARCH_ONLY_NOT_VALIDATED','metrics':m,**r},ensure_ascii=False,indent=2,allow_nan=False,default=convert))
    print(json.dumps({'status':'RESEARCH_ONLY_NOT_VALIDATED','strategy':cfg.name,'metrics':m},ensure_ascii=False,indent=2,allow_nan=False,default=convert))
'''
    (OUT/'final_strategy.py').write_text((OUT/'frozen_strategy.py').read_text()+'\nFROZEN_CONFIG='+repr(s['selection']['config'])+'\n'+footer)
    # Standalone scientific chart, and the same standard Plotly runtime in dashboard.
    fig=make_subplots(rows=2,cols=1,shared_xaxes=True,row_heights=[.65,.35],vertical_spacing=.09)
    for name,label,r in [(selected,'Preselected impulse',allr[selected]['test']),('value_impulse','Value + impulse (diagnostic)',allr['value_impulse']['test']),('ema_control','EMA trend control',bench['ema_control']['test']),('buy_hold','BTC buy & hold (100% invested)',bench['buy_hold']['test'])]:
        eq=np.asarray(r['equity'])/100000;dd=eq/np.maximum.accumulate(np.r_[1.,eq])[1:]-1
        fig.add_trace(go.Scatter(x=r['dates'],y=eq,name=label),row=1,col=1)
        fig.add_trace(go.Scatter(x=r['dates'],y=dd*100,name=label,showlegend=False),row=2,col=1)
    fig.update_layout(template='plotly_dark',title='BTC 1D held-out 2025–2026: actual cash + position NAV, unequal exposure',height=720)
    fig.update_yaxes(title_text='NAV / initial',row=1,col=1);fig.update_yaxes(title_text='Drawdown %',row=2,col=1)
    fig.write_html(OUT/'留出净值.html',include_plotlyjs=True)
    payload={'summary':s,'results':allr,'benchmarks':bench,'decision':decision,'bounds':json.loads((OUT/'execution_bounds.json').read_text()),'report':report,'rules':rules,'code':(OUT/'final_strategy.py').read_text()}
    template=(HERE/'report.template.html').read_text();target=ROOT/'apps/chanlun-web/analysis/elder-btc-1d-2026-11-12.html'
    target.write_text(template.replace('/*__DATA__*/',json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('</','<\\/'))
        .replace('/*__PLOTLY__*/',get_plotlyjs()).replace('<!--__RULES__-->',render_rules(rules)))
    files=['selection.json','summary.json','all_results.json','final_config.json','final_strategy.py','frozen_strategy.py','研究结论.md','书中逻辑与量化规则.md','execution_bounds.json','data_quality.json','execution-audit.json']
    (OUT/'artifact_hashes.json').write_text(json.dumps({f:hashlib.sha256((OUT/f).read_bytes()).hexdigest() for f in files},indent=2))
    print('BUILT',target,round(target.stat().st_size/1e6,2),'MB; report and standalone executable saved')

if __name__=='__main__':main()
