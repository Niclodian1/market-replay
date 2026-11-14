from pathlib import Path
import json,html,shutil
ROOT=Path(__file__).resolve().parents[4];OUT=ROOT/'reports/ma20-ai-v2-2026-11-04';r=json.loads((OUT/'report.json').read_text());m=json.loads((OUT/'model.json').read_text());DOC=ROOT/'apps/chanlun-web/docs'
snapshot=OUT/'snapshots'/m['version'];snapshot.mkdir(parents=True,exist_ok=True)
for name in ['model.json','report.json','parity.json','inputs.json','annotations.json','feedback.json','v1-model.json']:
 if not (snapshot/name).exists():shutil.copyfile(OUT/name,snapshot/name)
esc=lambda x:html.escape(str(x));pct=lambda x:'—'if x is None else f'{100*x:.1f}%'
def table(h,rows):return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+esc(v)+'</th>'for v in h)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(v)+'</td>'for v in row)+'</tr>'for row in rows)+'</tbody></table></div>'
names={'original_labels':'原标签 + 原26特征','updated_labels':'更新标签 + 原26特征','updated_structure':'更新标签 + 33特征'}
rows=[]
for k,v in r['experiments'].items():
 a=v['aggregate'];rows.append([names[k],f"{a['tp']}/{a['positive_n']} · {pct(a['recall'])}",f"{a['tn']}/{a['legacy_or_review_negative_n']} · {pct(a['negative_rejection'])}",f"{a['confirmed_negative_rejected']}/{a['confirmed_negative_n']}",pct(a['precision'])])
a=r['manualRegionAudit'];current=r['experiments'][r['selectedFeatureSet']]
body=f'''<div class="eyebrow">MA20 PREFERENCE LEARNING / V2 / 2026.11.04</div><h1>修正训练答案，核对结构，再做新标签盲检</h1><p class="lead">上一轮20条已合并。新增结构特征没有在开发检查中带来稳定改善，本版评分仍采用更新标签后的26特征模型；自动结构值仅作研究展示。</p><p><a href="../index.html?ma20ai=1">打开主图 · 本轮20条盲检 ↗</a> · <a href="ma20-ai-2026-11-04.html">查看第一版记录</a></p>
<section><h2>现在的状态：辅助复核，不自动过滤</h2><p>这20条反馈已被用于开发，不能再称为新模型的独立测试。旧“2024年以后”检查段也已参与本轮结构分析，不再作为全新检验。下面的向前检查用于比较开发方案，仍有回顾性设计影响。</p><p class="callout">真正等待的是另一批20条新标签：整段行情已在训练前预留，从全部实验的训练、验证和开发检查中排除。标注前不显示该点评分、加减分因素或分段颜色，也不让它进入高低分筛选列表。该盲检仍是历史图形上的新标签检验，不是新行情的前瞻实盘证据。</p></section>
<section><h2>1. 标签已按这轮反馈整理</h2><p>全局{r['labels']['positive']}个确认买点、{r['labels']['confirmedNegative']}个新确认反例、{r['labels']['weakNegative']}个待确认旧否定。旧否定仍用0.35权重，未知没有补成负样本。最终拟合使用{current['fit_n']}个开发标签，其中{current['fit_positive']}个正样本、{current['fit_confirmed_negative']}个确认反例、{current['fit_weak_negative']}个弱反例；其余标签因所在组被预留而排除。</p><p>原模型在这批分歧中保留9个会做、放过5个不做，误筛4个会做、过滤2个不做。这是主动选择的20条分歧，不是随机抽样的整体准确率。13个新会做中11个曾出现旧否定，说明不能把旧否定当作稳定真值。</p></section>
<section><h2>2. 三个对照，公开未改善的结果</h2><p>固定原来的逻辑回归和浅层梯度提升配置，没有按收益调参。相邻标注/候选间隔不超过21自然日归入一组，整个组只属于一个年度段。例如2024年3月的三个反例在同组，2023年12月25/27日也在同组。按组结束年份逐年向前：用更早年份训练、前一年度验证选门槛、下一年度做开发检查。</p>{table(['方案','买点保留','否定过滤（多数仍为旧标注）','确认反例过滤','已标注样本精确率'],rows)}
<p>更新标签和新增结构的两组拥有相同检查样本，可直接对照；原标签方案的“正确答案”及数量不同，不能把精确率变化全归因于模型进步。原始7个确认反例只有5个行情组，检查中可覆盖6个反例，统计仍很不稳定。</p><p>预先约定：结构组只有在保留率≥85%、较简单组下降不超过2个百分点、否定过滤增加至少5个百分点时才进入评分。本轮没有达到，保留简单组。最终模型对全部非预留开发组重新拟合；门槛采用各向前验证门槛中位数，原始评分对齐是否可靠，也要等待新盲检。</p></section>
<section><h2>3. 新增7个结构特征：表达你的理由，但不是硬过滤</h2><ul><li>距此前60根低点的反弹幅度；前段上涨幅度。</li><li>回撤占前段上涨的比例：(参考高点−当前收盘)/(参考高点−参考起涨低点)。</li><li>调整时间/前段上涨时间；调整后半与前半真实波幅比。</li><li>DIF/ATR；低点后整理时间/此前下跌时间。</li></ul><p>参照高点取此前60根最高价，起涨低点从该高点之前最多120根中寻找，调整只用高点以后、信号以前的数据。它们是因果可计算的代理，不代表已经识别了你认定的真实波段。调整不足6根时波幅比记未知；回撤比可超过100%，表示跌破该参考上涨起点，不直接判不做。</p><p>例如2024-03-17、20、25日，自动调整/前段上涨的时间比分别约0.03、0.05、0.09，提供了“刚涨完、整理很短”的信息。但有了这些数值，当前小模型仍未稳定学会接受与拒绝的区别，不能因此宣布已经量化成功。</p></section>
<section><h2>4. 自动段与37个人工区间核对</h2><p>区间交并比IoU的中位数约{pct(a['medianIoU'])}。可比较的{a['directionComparable']}个区间中，波幅收窄方向一致{a['sameContractionDirection']}个。多数手选区间本来就在收窄，因此这个方向一致数不能当作结构识别准确率。</p><p>这说明参照段仍只近似你的画法。前端现在可以在图上查看自动参考低A、参考高B、段内低C，但不会把它们自动填入你的手选表单。你认可或修正的区间继续单独记录。</p></section>
<section><h2>5. 止损率和最大运行盈利：向前检查的候选路径</h2><p>只看对应检查年度、非预留组的全部V3候选，包括未有人工标签的候选。信号收盘买入、同根低点固定止损，120自然日内不设均线卖出或止盈，MFE取止损前保守下界；未成熟窗口排除。指标未用作模型输入或选优目标。</p>
'''
for k,c in r['developmentPath120days'].items():
 body+=f'<h3>{esc(names[k])}</h3>'+table(['分组','成熟候选','止损率','MFE中位数','MFE平均值','MFE P90'],[[label,v['n'],pct(v.get('stop_rate')),pct(v.get('mfe_median')),pct(v.get('mfe_mean')),pct(v.get('mfe_p90'))]for label,v in c.items()])
body+='''<p>这些指标没有证明当前筛选能改善趋势捕捉能力，所以不把模拟保留列表当成正式交易信号。样本数量和行情分布也随筛选变化。</p></section><section><h2>6. 上轮20条：更新后分数仅作拟合诊断</h2><p>所有20条都在开发拟合中。下表即使看起来更接近你的答案，也不能作为泛化证明。重点对照四个低分正例与七个反例的结构差异。</p>'''
body+=table(['日期','你的判定','v1分数','v2分数（开发拟合）','备注'],[[x['date'],'会做'if x['decision']=='yes'else'不做',f"{x['oldScore']*100:.0f}",f"{x['newScore']*100:.0f}",x['note']or'、'.join(x['reasons'])or'—']for x in r['feedbackRows']])
body+=f'''</section><section><h2>7. 现在怎么复核</h2><ol><li>顶部“AI买点”默认打开“本轮20条·整组留出盲检”。这20条在训练之前冻结，不会随评分改变。</li><li>每条先独立判断会做/不做/跳过；不做保留一条原因。提交后才可查看该点分数。系统保留第一次盲检判断；之后改判会标记为已修改，盲检仍使用首次答案。</li><li>对波段有疑问，可展开自动参照并在图上查看；你自行选择的调整区间与自动段分开保存。</li><li>标完导出JSON。本轮结果先对冻结模型做检查，再决定是否进入下一版训练，不能先把盲检答案喂回模型再计算效果。</li><li>“开发分歧”是另一组补标入口，排除全部预留组，不与盲检混用。</li></ol><p>预留了{len(r['reservedGroups'])}个行情组，20条候选均至少有120根历史上下文。优先选没有当前确认标签的组；不足时额外预留含旧正例的组，上一轮20条反馈所在组均保留在开发中。这批预留组的所有旧标签也未用于拟合或开发检查。</p><p>当前仅BTC日线。其他品种不套用该模型的分数或历史指标。</p></section>
<section><h2>复现与版本</h2><pre>python3 apps/chanlun-web/tools/ma20-ai/train_v2.py
python3 apps/chanlun-web/tools/ma20-ai/build_report_v2.py

# 收到本轮盲检导出后，先检查冻结模型，不重训：
python3 apps/chanlun-web/tools/ma20-ai/evaluate_blind.py --feedback '/导出文件.json'</pre><p>模型：{esc(m['version'])}<br>反馈SHA256：{r['inputs']['feedbackSha256']}<br>行情SHA256：{r['inputs']['marketSha256']}</p><p>本轮输入、标注、分组、逐折预测、对照结果、模型快照保存于 reports/ma20-ai-v2-2026-11-04/。旧v1模型保留，浏览器新标注使用独立的ma20_ai_v2来源。分数始终是未校准的偏好排序，不是盈利概率。</p></section>'''
style='''<style>:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f3f5ef;color:#18313c;font:15px/1.8 system-ui,-apple-system,"PingFang SC",sans-serif}main{max-width:1120px;margin:auto;padding:40px 24px 80px}h1{font-size:32px;line-height:1.4}h2{font-size:22px}h3{font-size:17px}section{background:white;border:1px solid #d9e3dc;border-radius:14px;padding:25px;margin:24px 0}.eyebrow{font-size:12px;color:#087b68;letter-spacing:2px}.lead{font-size:18px;color:#597178}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:10px;border-bottom:1px solid #d9e3dc;text-align:left;vertical-align:top}th{white-space:nowrap}.callout{padding:12px 16px;background:#f7eee2;border-left:3px solid #b77a2c}a{color:#087b68}p{overflow-wrap:anywhere}pre{overflow:auto;background:#f3f5ef;padding:15px;font-size:12px}@media(max-width:700px){main{padding:22px 12px}section{padding:17px}h1{font-size:26px}}</style>'''
(DOC/'ma20-ai-v2-2026-11-04.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI买点 v2 · 标签修正、结构对照与新盲检</title>'+style+'<main>'+body+'</main></html>')
print(DOC/'ma20-ai-v2-2026-11-04.html')
