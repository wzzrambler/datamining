"""Fully automatic P3 pipeline. Inputs contain no route-selection coordinates."""
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from discovery import discover_all, automatic_pairs
from parser import period_for_clock

BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from artifact_integrity import matches_sha256
OUT=ROOT/'processed/P3路线层级挖掘'
DISPLAY=ROOT/'outputs/P3'
INPUTS={'p1':ROOT/'processed/P1爬虫数据清洗/documents.json',
        'p2':ROOT/'processed/P2实体识别/entities.json',
        'sources':ROOT/'processed/P1爬虫数据清洗/辅助资料/paragraph_sources.json'}

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def render_readable(routes,pairs,survey,summary):
    lines=['# P3 自动路线结果与时间层级','','所有路线由程序扫描P1全文并关联P2 v2生成；没有预设文档、段号、原句或配对。路线性质也是规则判断，未进行人工验收。','',
           f"扫描{summary['documents_scanned']}篇，{summary['selected_documents']}篇输出{summary['routes']}条路线、{summary['steps']}步。",'']
    for r in routes:
        lines += [f"## {r['route_id']} {r['title']}",'',f"格式：{r['format']}；性质：{r['nature']}；范围：{r['scope']}；粒度：{r['granularity']}。",'',
                  '|步骤|日序/时段|原文核心|路线POI|','|---|---|---|---|']
        for s in r['steps']:
            pois='、'.join(p['canonical_name'] or p['text']+'（未归一）' for p in s['route_poi_refs'])
            pois+=''.join('、'+p['canonical_name']+'（自动简称链接）' for p in s['p3_normalized_links'])
            text=s['evidence']['text'].replace('|','／').replace('\n',' ')
            lines.append(f"|{s['index']}|{s['time']['day'] if s['time']['day'] is not None else '未知日序'}/{s['time']['period']}|{text}|{pois.strip('、') or '未识别，保留原文'}|")
        lines += ['', '时间层级：','']
        for d in r['hierarchy']:
            lines.append('- 日序 '+str(d['day'] if d['day'] is not None else '未知')+'：'+' → '.join(f"{g['period']}（{len(g['step_ids'])}步）" for g in d['period_groups']))
        lines += ['', '局限：'+'；'.join(r['limits']),'']
    (DISPLAY/'路线结果与时间层级.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    lines=['# P3实验报告正文','','## 方法与输入','',
      '采用全文规则扫描，参考旧版官方路线解析机制，并扩展到所有游客文档。输入仅P1正文、P2 v2实体和段落来源；没有路线配置文件、指定文档ID、段号、原句、日序、别名定位或人工配对。运行中计算的位置仅作为证据输出及内部状态，不是预设参数。P2的输入哈希必须与P1匹配。','',
      '每个正文、每张OCR图片和评论分别维护路线标题、日序、时段和格式状态。遇新路线标题、非路线主题及来源边界自动切分；Day/第几天更新日序。编号、时间段、箭头、明确顺序列表、带连接词且跟随P2地点的叙述句生成步骤。没有格式或实体支撑时写入拒绝原因，不猜测补全。','',
      '同段内保留移动段共同时间。时段优先遵循原文标题，钟点映射采用06:00—11:59上午、12:00—13:29中午、13:30—17:59下午、18:00后晚上，06:00前夜间。日序、时段缺失为null/未说明；活动保存字面动作，不根据时刻差推造停留时长。','',
      '名称复用P2；仅对完整节点进行同景区唯一标准名后缀匹配（至少4字），自动记录目标P2实体和源位置。未知标准名和父子节点不强行合并。摘要与编号详情、正文与OCR副本按同文档、相同日序/粒度、POI序列相似度自动去重，并保留被抑制候选及原因。该去重可能误判相似但不同路线，原证据仍可检查。','',
      '非路线主题、外部交通、只有角色晋级等箭头、否定或建议边片段，以及步骤/POI不足的候选均保留拒绝日志。OCR断行和词典外地点仍可能造成漏识别。','',
      '## 运行结果','',
      f"本轮扫描{summary['documents_scanned']}篇，{summary['selected_documents']}篇自动生成{summary['routes']}条路线、{summary['steps']}步，自动配对{summary['comparisons']}组。",'',
      '性质分布：'+json.dumps(summary['routes_by_nature'],ensure_ascii=False)+'。','',
      '时间分布：'+json.dumps(summary['steps_by_period'],ensure_ascii=False)+'。步骤数量依解析形式而变化，不能解释成时间分配。','',
      '## 自动配对与指标','',
      '遍历全部官方/游客路线组合，要求同景区、同范围、同粒度且至少两个共同游览POI；不预设固定文档或最佳案例。游览POI排除配套及景区外节点。覆盖度=共同POI/官方POI，Jaccard=交集/并集。顺序先投影到共同POI，再计算LCS/max长度；无序组和未决分支不计算顺序指标。保留非连续重访，仅折叠相邻重复。','',
      '游客实际到访仅为原文自述触发的自动判断visitor_actual_claim；推荐/参考和无法判定分别标识，不能视为独立事实核验。范围或粒度无法确定的路线不配对。','',
      '|官方路线|游客路线|游客性质|共同POI|覆盖度|Jaccard|共同顺序相似度|','|---|---|---|---|---|---|---|']
    for c in pairs:
        order=f"{c['common_order_similarity']:.3f}" if c['common_order_similarity'] is not None else '不适用'
        lines.append(f"|{c['official_route_id']}|{c['visitor_route_id']}|{c['visitor_nature']}|{len(c['shared'])}|{c['official_coverage']:.3f}|{c['jaccard']:.3f}|{order}|")
    lines += ['', '完整差异集合、序列和自动分析见route_compare.json。差异可来自选点、顺序、行程范围及规则漏识别；结构差异本身不能证明因果或游客总体偏好。','',
      '## 失败与局限','',
      '1. 同一文本可能有摘要、详情和OCR副本。若全部拼接会产生虚假的重访，采用序列重叠和来源优先级去重；相似路线仍可能误合并，日志保留原片段。','',
      '2. 原文可有时段重叠、标题与钟点分段差异。程序输出诊断并保留原值，不修改为看似合理的时间。','',
      '3. 文本中的“先……再……”可能是建议或假设，程序通过否定/条件词保守排除；词典外地点、OCR断行和隐含顺序仍可能漏识别。自动化不表示语义准确率100%。','',
      '4. 官方只提供园区级介绍时，无法自动得到馆舍级官方路线；跨粒度不配对。活动名称缺少POI命中时保留步骤和未决项，不造地点。','',
      '## 核对与复现','',
      '质量核对记录.json由程序检查所有路线的原文位置、P2引用、层级和关系；测试另验证随机ID/段号变动、输入扩展和通用格式。没有人工金标准，不报告P/R/F1。','',
      '```powershell','python "src/P3路线层级挖掘/run.py"','python -m unittest discover -s tests/P3 -v','```','',
      '核心解析仅Python标准库，绘图Matplotlib；无需模型接口和人工配置。结果为processed/P3路线层级挖掘，可读表、报告及PNG/SVG图在outputs/P3。']
    (DISPLAY/'P3实验报告正文.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def run(plots=True):
    paths=INPUTS;input_hashes={k:digest(p) for k,p in paths.items()}
    documents=json.loads(paths['p1'].read_text('utf-8'))
    entities=json.loads(paths['p2'].read_text('utf-8'))
    if entities['config_version']!='v2' or not matches_sha256(paths['p1'], entities['input_sha256']):
        raise ValueError('P2 v2必须与本次P1正文对应')
    if entities.get('origin_sha256') and not matches_sha256(paths['sources'], entities['origin_sha256']):
        raise ValueError('P2来源映射与P1段落来源不对应')
    edocs={d['doc_id']:d for d in entities['documents']}
    if {d['doc_id'] for d in documents}!=set(edocs):raise ValueError('P1/P2文档不一致')
    sources={(s['doc_id'],s['para_id']):s for s in json.loads(paths['sources'].read_text('utf-8'))}
    routes,survey=discover_all(documents,edocs,sources)
    checks=validate(routes,documents,edocs)
    notes=diagnostics(routes)
    pairs=automatic_pairs(routes)
    OUT.mkdir(exist_ok=True);DISPLAY.mkdir(exist_ok=True)
    summary=dict(documents_scanned=len(documents),selected_documents=len({r['doc_id'] for r in routes}),
        routes=len(routes),steps=checks['steps'],candidates=sum(d['candidate_count'] for d in survey),comparisons=len(pairs),
        routes_by_nature=dict(Counter(r['nature'] for r in routes)),
        routes_by_format=dict(Counter(r['format'] for r in routes)),
        steps_by_period=dict(Counter(s['time']['period'] for r in routes for s in r['steps'])),
        candidate_status=dict(Counter(x['status'] for d in survey for x in d['candidates'])),diagnostics=len(notes),
        automation='full_text_rules_no_preselected_locations',independent_semantic_accuracy=None)
    for filename,value in [('routes.json',dict(schema_version='p3-routes-2',p2_config_version='v2',automation=summary['automation'],routes=routes)),
                           ('candidate_inventory.json',survey),('route_compare.json',pairs),('diagnostics.json',notes),('summary.json',summary),('validation.json',checks)]:dump(OUT/filename,value)
    fields=['route_id','doc_id','step_id','index','day','period','time_basis','clock_texts','start','end','text','pois','p3_normalized_pois','activities','transports','durations','unresolved']
    with (OUT/'route_steps.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for r in routes:
            for s in r['steps']:
                writer.writerow(dict(route_id=r['route_id'],doc_id=r['doc_id'],step_id=s['step_id'],index=s['index'],day=s['time']['day'],period=s['time']['period'],time_basis=s['time']['basis'],clock_texts='|'.join(s['time']['clock_texts']),start=s['evidence']['start'],end=s['evidence']['end'],text=s['evidence']['text'],pois='|'.join(p['canonical_name'] or p['text'] for p in s['route_poi_refs']),p3_normalized_pois='|'.join(p['canonical_name'] for p in s['p3_normalized_links']),activities='|'.join(a['text'] for a in s['activity']),transports='|'.join(t['text'] for t in s['transport_refs']),durations='|'.join(d['text']+':'+d['role'] for d in s['durations']),unresolved='|'.join(s['unresolved'])))
    dump(DISPLAY/'质量核对记录.json',dict(review_type='automatic_integrity_checks',human_gold_status='not_created',
         automation=summary['automation'],checks=checks,diagnostic_counts=dict(Counter(n['kind'] for n in notes)),
         note='自动核对所有结构与位置；输出候选和拒绝原因，没有AI预选段。不是人工语义准确率。'))
    render_readable(routes,pairs,survey,summary)
    if plots:plot(routes,pairs)
    for k,p in paths.items():
        if digest(p)!=input_hashes[k]:raise RuntimeError('输入被改变')
    dump(OUT/'run_manifest.json',dict(implementation_version='2.0.0',python=sys.version.split()[0],
         inputs={k:str(p.relative_to(ROOT)) for k,p in paths.items()},input_sha256=input_hashes,
         implementation_sha256={p.name:digest(p) for p in sorted(BASE.glob('*.py'))},
         automation=summary['automation'],preselected_location_config=False,offline=True,
         outputs_sha256={p.name:digest(p) for p in sorted(OUT.glob('*')) if p.is_file() and p.name!='run_manifest.json'},
         display_sha256={str(p.relative_to(DISPLAY)):digest(p) for p in sorted(DISPLAY.rglob('*')) if p.is_file()}))
    print(json.dumps(summary,ensure_ascii=False))

def validate(routes, documents, entity_docs):
    docs = {d['doc_id']: d for d in documents}
    mentions = {m['mention_id']: m for d in entity_docs.values() for m in d['mentions']}
    counts = Counter()
    for r in routes:
        doc = docs[r['doc_id']]
        local_ids = {m['mention_id'] for m in entity_docs[r['doc_id']]['mentions']}
        ids = {s['step_id'] for s in r['steps']}
        assert len(ids) == len(r['steps'])
        for s in r['steps']:
            evs = [s['evidence']] + s['context_evidence']
            evs += [s['time'][k] for k in ('evidence', 'day_evidence') if s['time'][k]]
            evs += [d['evidence'] for d in s['durations']]
            for ev in evs:
                assert doc['content'][ev['start']:ev['end']] == ev['text']
                counts['evidence_spans_checked'] += 1
            assert set(s['p2_mention_ids']) <= local_ids
            for ref in s['poi_refs']:
                m = mentions[ref['mention_id']]
                assert ref['mention_id'] in s['p2_mention_ids']
                assert s['evidence']['start'] <= m['start'] < m['end'] <= s['evidence']['end']
                assert m['text'] == doc['content'][m['start']:m['end']]
                counts['poi_references_checked'] += 1
            for action in s['activity']:
                assert doc['content'][action['start']:action['end']] == action['text']
            for link in s['p3_normalized_links']:
                assert doc['content'][link['start']:link['end']] == link['text']
                target = mentions[link['target_p2_mention_id']]
                assert target['type'] == 'POI' and target['canonical_name'] == link['canonical_name']
                target_doc = next(d for d in entity_docs.values() if any(m['mention_id'] == target['mention_id'] for m in d['mentions']))
                assert target_doc['scenic_code'] == r['scenic_code']
        for edge in r['order_relations']:
            assert edge['before'] in ids and edge['after'] in ids
            assert edge['before'] != edge['after']
        grouped = [sid for d in r['hierarchy'] for g in d['period_groups'] for sid in g['step_ids']]
        assert grouped == [s['step_id'] for s in r['steps']]
    return {'status': 'passed', 'routes': len(routes), 'steps': sum(len(r['steps']) for r in routes),
            **counts, 'meaning': '原文位置、P2引用、步骤关系及时间层级一致性；不等于语义准确率。'}


def diagnostics(routes):
    warnings = []
    for r in routes:
        last_end = None
        last_day = None
        for s in r['steps']:
            t = s['time']
            if t['basis'] == 'explicit_heading' and t['start_minute'] is not None:
                mapped = period_for_clock(t['start_minute']//60, t['start_minute']%60)
                if mapped != t['period']:
                    warnings.append({'route_id': r['route_id'], 'step_id': s['step_id'],
                        'kind': 'heading_clock_difference', 'heading_period': t['period'],
                        'clock_mapped_period': mapped, 'action': '原文标题优先，钟点同时保留，分段规则差异不直接认定原文错误'})
            if t['day'] != last_day: last_end = None
            if t['start_minute'] is not None and last_end is not None and t['start_minute'] < last_end:
                warnings.append({'route_id': r['route_id'], 'step_id': s['step_id'], 'kind': 'time_overlap_in_source',
                                 'text': s['evidence']['text'], 'previous_end_minute': last_end,
                                 'action': '保留原文，不推算或改写时间'})
            if t['end_minute'] is not None: last_end = t['end_minute']
            last_day = t['day']
            for ref in s['poi_refs']:
                if not ref['canonical_name']:
                    warnings.append({'route_id': r['route_id'], 'step_id': s['step_id'],
                                     'kind': 'unresolved_poi_name', 'text': ref['text'], 'action': '保留P2原始名，不模糊合并'})
    return warnings


def plot(routes, comparisons):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    names = {f.name for f in font_manager.fontManager.ttflist}
    font = next((x for x in ['Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC', 'SimSun'] if x in names), 'DejaVu Sans')
    plt.rcParams.update({'font.sans-serif': [font], 'axes.unicode_minus': False, 'svg.fonttype': 'none', 'svg.hashsalt': 'p3-fixed'})
    folder = DISPLAY/'图表'; folder.mkdir(exist_ok=True)
    selected = []
    for code in sorted({r['scenic_code'] for r in routes}):
        ranked = sorted([r for r in routes if r['scenic_code']==code], key=lambda r:(sum(s['time']['period']!='未说明' for s in r['steps']),r['source_text_type']=='official_route'), reverse=True)
        if ranked: selected.append(ranked[0])
    if not selected: return
    fig, axes = plt.subplots(len(selected), 1, figsize=(14, 3.4*len(selected)), squeeze=False)
    colors = {'上午':'#75a9cf','中午':'#e7bd67','下午':'#80b9a4','晚上':'#a49ac2','未说明':'#bec3c8'}
    for ax, r in zip(axes[:,0], selected):
        for s in r['steps']:
            ax.barh(0, 1, left=s['index']-1, color=colors.get(s['time']['period'],'#a8adb5'), edgecolor='white')
            ax.text(s['index']-.5, 0, str(s['index']), ha='center', va='center', fontsize=8)
        title=''.join(c for c in r['title'] if ord(c)<0x2600 or '\u4e00'<=c<='\u9fff')
        ax.set(xlim=(0,len(r['steps'])), yticks=[], xlabel='步骤序号（宽度不代表时长）', title=title)
        ax.spines[['top','right','left']].set_visible(False)
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(color=v,label=k) for k,v in colors.items()], loc='lower center', ncol=len(colors))
    fig.tight_layout(rect=(0,.06,1,1))
    for ext in ('png','svg'): fig.savefig(folder/f'时间层级概览.{ext}',dpi=160,metadata={'Date':None} if ext=='svg' else None)
    plt.close(fig)
    if not comparisons: return
    fig, ax = plt.subplots(figsize=(11,5))
    shown = sorted(comparisons, key=lambda c:(len(c['shared']),c['jaccard']),reverse=True)[:8]
    for i,c in enumerate(shown):
        ax.bar(i-.18,c['official_coverage'],.34,color='#39789b',label='官方景点覆盖度' if i==0 else None)
        ax.bar(i+.18,c['jaccard'],.34,color='#d9aa4a',label='Jaccard' if i==0 else None)
    ax.set(xticks=range(len(shown)),xticklabels=[str(i+1) for i in range(len(shown))],ylim=(0,1),ylabel='比例', xlabel='自动配对排名（按共同POI数、Jaccard；对应图表/对比图索引.json）', title='同范围路线的POI集合对比（仅当前样本）')
    dump(folder/'对比图索引.json', [{'rank':i+1, 'official_route_id':c['official_route_id'],'visitor_route_id':c['visitor_route_id']} for i,c in enumerate(shown)])
    ax.legend(); fig.tight_layout()
    for ext in ('png','svg'): fig.savefig(folder/f'路线对比.{ext}',dpi=160,metadata={'Date':None} if ext=='svg' else None)
    plt.close(fig)


