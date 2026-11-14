"""Build a standalone reviewable report from the frozen model artifact."""
from pathlib import Path
import json,html
ROOT=Path(__file__).resolve().parents[4];p=ROOT/'apps/chanlun-web/js/13-ma20-ai-model.js';m=json.loads(p.read_text().split(' = ',1)[1].strip().rstrip(';'));r=m['report'];docs=ROOT/'apps/chanlun-web/docs'
def pct(v):return '—' if v is None else f'{v*100:.1f}%'
def table(h,rows):return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+html.escape(str(v))+'</th>'for v in h)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>'for v in row)+'</tr>'for row in rows)+'</tbody></table></div>'
selected=r['models'][r['chosen']]['splits']['test'];gate_text='历史标签门槛达到，仍需独立复核' if r['gate_passed'] else '尚未通过过滤要求'
rows=[]
for name,v in r['models'].items():
 for phase,stat in v['splits'].items():rows.append([name,phase,stat['n'],f"{stat['tp']}/{stat['positive_n']} · {pct(stat['recall'])}",f"{stat['tn']}/{stat['legacy_or_review_negative_n']} · {pct(stat['negative_rejection'])}",pct(stat['precision'])])
stage=r['stage_test'];paths=r['test120days'];content=f'''<div class="eyebrow">PREFERENCE LEARNING / BTC DAILY / 2026.11.04</div><h1>AI学习买点：第一版已训练，{gate_text}</h1>
<p class="lead">当前用途是帮助复核分歧，保留原始候选入口。首轮没有证明能在保留大部分买点的同时稳定降低噪声。</p>
<p><a href="../index.html?ma20ai=1">打开主图 · AI买点 ↗</a></p>
<section><h2>先看没有参与选模型的较晚历史段</h2><p>验证段选出{html.escape(r['chosen'])}，门槛在验证段确定。2024年以后的已标注样本中，模型保留{selected['tp']}/{selected['positive_n']}个买点（{pct(selected['recall'])}），过滤{selected['tn']}/{selected['legacy_or_review_negative_n']}个否定位置（{pct(selected['negative_rejection'])}，多数仍为旧标注）。当前状态：{gate_text}；预设双条件为买点保留≥85%、否定样本过滤≥20%。</p><p class="callout">当前是学习和复核版本。分数是未校准的偏好排序，不是盈利概率。历史检查段也属于回看标注，不能当作前瞻实盘证明。</p></section>
<section><h2>训练数据没有把未知位置当负样本</h2><p>当前正样本{r['labels']['positive']}个；旧轮次无冲突的否定位置{r['labels']['weak_negative']}个，作为权重0.35的临时负样本；新复核确认记录{r['labels']['confirmed_new_review']}个；其他{r['labels']['unknown_records']}个已标注时间保持未知。其他未标注K线全部保持未知。新确认买点覆盖旧否定；反馈中出现新的明确冲突则转为待复核，不静默覆盖。</p>
{table(['分段','时间','样本数','正 / 负'],[[k,{'train':'截至2021-06-30','validation':'2022-01-01 — 2023-06-30','test':'2024-01-01以后'}[k],v['n'],f"{v['positive']} / {v['negative']}"]for k,v in r['splitCounts'].items()])}
<p>时间边界间留约半年，{r['excluded_boundary_records']}个标注位置位于边界区而不用于这次训练或检查。特征来自当前及此前K线，未用标签文本、手工区间边界、未来止损或MFE当作输入。缺失值填充、均值、尺度只在训练段拟合。</p></section>
<section><h2>两个固定小模型，没有根据检查结果反复调参</h2><p>逻辑回归C=0.3；浅层梯度提升树40棵、深度2、叶子最少12个样本。当前环境使用已有scikit-learn实现，未新增CatBoost依赖。先以验证段保留≥90%买点确定各自门槛，再按验证段精确率选模型；没有按未来涨幅选模型。</p>
{table(['模型','分段','样本','买点保留','旧否定过滤','已标注样本中的精确率'],rows)}
<p>请比较训练、验证和后段检查：当前首轮训练与验证好于后段，尚不足以证明稳定概括你的选择。没有因为逻辑回归在后段保留更多就事后换赢家。下一轮补充新反馈后必须版本化重训，并保留独立检查样本。</p></section>
<section><h2>模型过滤与原候选池漏选分开计算</h2><p>较晚历史段共有{stage['all_candidates']}个V3候选，模型模拟保留{stage['retained']}个。其中属于你确认买点的候选是{stage['known_positive_candidates']}个，模拟保留{stage['retained_known_positive_candidates']}个。该阶段总共有{selected['positive_n']}个已确认买点，原候选池先漏掉{selected['positive_n']-stage['known_positive_candidates']}个，模型又低估{stage['known_positive_candidates']-stage['retained_known_positive_candidates']}个，不能把两种漏选混成一个好看的覆盖率。</p><p>前端提供“我的买点·被模型低估”和“我的买点·原候选池漏选”两个列表；模型只筛选候选，当前无法自动找回候选池之外的买点。</p></section>
<section><h2>止损率和最大运行空间：只作诊断</h2><p>2024以后全部候选与模拟保留候选分别统计120自然日。收盘入场、同根低点固定止损，没有均线卖出或固定止盈，止损当根取保守高低先后。未成熟窗口排除，百分比是未扣成本的价格路径。</p>
{table(['分组','成熟样本','止损率','MFE中位数','MFE平均值','MFE P90'],[[name,v['n'],pct(v.get('stop_rate')),pct(v.get('mfe_median')),pct(v.get('mfe_mean')),pct(v.get('mfe_p90'))]for name,v in paths.items()])}
<p>这点历史变化没有补偿买点漏选，也没有建立显著性证据，所以不把它称为模型已经有效。特征和分类训练没有使用上述未来结果。</p></section>
<section><h2>下一轮每批20条，怎样标</h2><ol><li>打开主图顶部“指标 → AI买点”，默认生成20条复核队列。</li><li>候选来自：旧否定但模型偏好高、接近门槛、两个模型意见差异大、固定随机对照。每组去重；本机已做判定的记录不会重新进入下一批。</li><li>进入本组标注后未来遮罩开启，默认隐藏模型分数；建议先独立判断，再展开模型意见。会做 / 不做 / 不确定按你当时看到的图形判断；不做保留至少一条理由或备注。</li><li>如果是区域问题，优先补充参照高点、调整起止；图上点选即可。不要因为后面上涨就把原本不认可的图形改成会做。</li><li>标完导出JSON，然后生成下一批。分数接近不等于样本一定最有信息，因此队列保留了随机对照。</li></ol><p>浏览器中的新反馈会保存和导出，不会假装自动训练。当前是离线、可审查的重训流程。使用导出的文件运行下面命令后刷新页面，模型与报告会更新；原始上传文件保持不变。</p>
<pre>python3 apps/chanlun-web/tools/ma20-ai/train.py --feedback '/你的导出文件.json'
python3 apps/chanlun-web/tools/ma20-ai/build_report.py</pre>
<p>可用 --annotations 指定新的主标注文件。当前训练及部署仅适用于BTC日线；ETH、黄金和股票暂不套用BTC的历史指标和门槛。后续应补充各品种明确标注并另行验证。</p></section>
<section><h2>输入与版本</h2><p>版本：{html.escape(m['version'])}<br>特征版本：{html.escape(m['featureVersion'])}<br>特征数：{len(m['featureNames'])}<br>标注SHA256：{r['annotations_sha256']}<br>行情SHA256：{r['market_sha256']}<br>scikit-learn：{r['sklearn_version']}</p><p>共享特征引擎用于离线训练与前端推理；模型参数与标签来源随版本冻结。全部验证数据和审计记录位于 reports/ma20-ai-2026-11-04/。原V3规则、人工标签以及其他策略不作为AI输出覆盖。</p></section>'''
style='''<style>:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f3f5ef;color:#18313c;font:15px/1.8 system-ui,-apple-system,"PingFang SC",sans-serif}main{max-width:1100px;margin:auto;padding:40px 24px 80px}h1{font-size:32px;line-height:1.4}h2{font-size:22px}section{background:white;border:1px solid #d9e3dc;border-radius:14px;padding:25px;margin:24px 0}.eyebrow{font-size:12px;color:#087b68;letter-spacing:2px}.lead{font-size:18px;color:#597178}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:10px;border-bottom:1px solid #d9e3dc;text-align:left}th{white-space:nowrap}.callout{padding:12px 16px;background:#f7eee2;border-left:3px solid #b77a2c}a{color:#087b68}p{overflow-wrap:anywhere}pre{overflow:auto;background:#f3f5ef;padding:15px;font-size:12px}@media(max-width:700px){main{padding:22px 12px}section{padding:17px}h1{font-size:26px}}</style>'''
(docs/'ma20-ai-2026-11-04.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI买点学习·首轮验证与复核</title>'+style+'<main>'+content+'</main></html>')
print(docs/'ma20-ai-2026-11-04.html')
if __name__=='__main__':pass
