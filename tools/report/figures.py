# -*- coding: utf-8 -*-
"""重画第 5、6 章的图。

之前那两张图是手工截的，数据换了一轮就对不上了。这里全部改成从产物现算：

    outputs/P5/图表/图5-1_陈述类型与景区.png
    outputs/P5/图表/图5-2_条件类别与人群.png
    outputs/P6/可视化可交互知识图谱/图6-1_九寨沟路线子图.png
    outputs/P6/可视化可交互知识图谱/图6-2_关系类型分布.png

    python -X utf8 tools/report/figures.py
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import networkx as nx
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[2]
P6 = ROOT / 'processed/P6知识图谱/cleaned-v1'
OUT5 = ROOT / 'outputs/P5/图表'
OUT6 = ROOT / 'outputs/P6/可视化可交互知识图谱'

for f in font_manager.fontManager.ttflist:
    if f.name == 'Microsoft YaHei':
        plt.rcParams['font.family'] = 'Microsoft YaHei'
        break
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['figure.dpi'] = 200
SCENE = {'GZCL': '广州长隆', 'ZHCL': '珠海长隆', 'JZG': '九寨沟'}
KIND = {'general_advice': '一般建议', 'conditional_advice': '条件建议',
        'conditional_fact': '条件事实', 'incomplete_condition': '条件不全',
        'general_restriction': '一般限制', 'conditional_restriction': '条件限制'}
CAT = {'date_time': '日期/时间', 'other': '其他', 'audience': '人群', 'ticket': '门票',
       'access': '进出园', 'crowding': '拥挤', 'time_budget': '时间预算',
       'weather': '天气', 'service_availability': '服务可用', 'fitness': '体力'}
AUD_ORDER = ['带小孩', '住店客人', '带长辈', '其他人群']


def bucket(text):
    t = text or ''
    if re.search('宝宝|娃|小孩|儿童|孩子|身高|亲子|小朋友|小盆友', t):
        return '带小孩'
    if re.search('住客|酒店|入住|住宿', t):
        return '住店客人'
    if re.search('老人|父母|长辈|爸妈', t):
        return '带长辈'
    return '其他人群'


def load(name):
    return json.loads((P6 / name).read_text('utf-8'))


def fig5(rules):
    kinds = list(KIND)
    scenes = [s for s in SCENE if any(r['scenic_code'] == s for r in rules)]
    counts = {s: Counter(r['statement_kind'] for r in rules if r['scenic_code'] == s) for s in scenes}
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    width = 0.8 / len(kinds)
    for i, k in enumerate(kinds):
        xs = [j + i * width + width / 2 for j in range(len(scenes))]
        ys = [counts[s].get(k, 0) for s in scenes]
        ax.bar(xs, ys, width=width, label=KIND[k])
        for x, y in zip(xs, ys):
            if y:
                ax.text(x, y + max(1, y * 0.01), str(y), ha='center', va='bottom', fontsize=6.5)
    ax.set_xticks([j + 0.4 for j in range(len(scenes))])
    ax.set_xticklabels([SCENE[s] for s in scenes], fontsize=8)
    ax.set_ylabel('条件/建议条数', fontsize=8)
    ax.tick_params(labelsize=7)
    ax.legend(fontsize=6.6, ncol=3, frameon=False, loc='upper right')
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    ax.set_title('六种陈述类型在三个景区的分布（共 %d 条）' % len(rules), fontsize=9)
    fig.tight_layout()
    OUT5.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT5 / '图5-1_陈述类型与景区.png', bbox_inches='tight')
    plt.close(fig)

    cats = Counter()
    for r in rules:
        for cl in ((r.get('condition') or {}).get('clauses') or []):
            cats[cl.get('category')] += 1
    auds = Counter(bucket(cl.get('text')) for r in rules
                   for cl in ((r.get('condition') or {}).get('clauses') or [])
                   if cl.get('category') == 'audience')
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.4), gridspec_kw={'width_ratios': [1.35, 1]})
    items = sorted(cats.items(), key=lambda kv: kv[1])
    a1.barh([CAT.get(k, k) for k, _ in items], [v for _, v in items], color='#4C78A8')
    for i, (_, v) in enumerate(items):
        a1.text(v + 2, i, str(v), va='center', fontsize=6.5)
    a1.set_title('条件子句的类别', fontsize=8.5)
    a1.tick_params(labelsize=7)
    a1.set_xlabel('子句数', fontsize=7.5)
    for side in ('top', 'right'):
        a1.spines[side].set_visible(False)
    a2.bar([AUD_ORDER.index(k) for k in AUD_ORDER], [auds.get(k, 0) for k in AUD_ORDER],
           color='#F58518')
    for i, k in enumerate(AUD_ORDER):
        a2.text(i, auds.get(k, 0) + 1, str(auds.get(k, 0)), ha='center', fontsize=6.5)
    a2.set_xticks(range(len(AUD_ORDER)))
    a2.set_xticklabels(AUD_ORDER, fontsize=7)
    a2.set_title('人群条件的关键词粗分', fontsize=8.5)
    a2.tick_params(labelsize=7)
    a2.set_ylabel('子句数', fontsize=7.5)
    for side in ('top', 'right'):
        a2.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT5 / '图5-2_条件类别与人群.png', bbox_inches='tight')
    plt.close(fig)
    return len(rules), cats, auds


def fig6(graph):
    nodes = {n['id']: n for n in graph['nodes']}
    rid = 'JZG-OFF-01-R-5489d330c8'
    steps = [e['target'] for e in graph['edges'] if e['type'] == 'HAS_STEP' and e['source'] == rid]
    steps.sort(key=lambda s: nodes[s]['properties']['index'])
    visits = defaultdict(list)
    for e in graph['edges']:
        if e['type'] == 'VISITS' and e['source'] in steps:
            visits[e['source']].append(nodes[e['target']]['properties'].get('name') or '')
    # 蛇形排布：每行 5 步，避免 15 个节点拉成一条线看不清
    per_row = 5
    fig, ax = plt.subplots(figsize=(7.2, 3.9))
    prev = set()
    for k, sid in enumerate(steps):
        row, col = divmod(k, per_row)
        x, y = col * 1.6, -row * 1.5
        prop = nodes[sid]['properties']
        times = (prop.get('time') or {}).get('clock_texts') or []
        ax.scatter([x], [y], s=210, color='#4C78A8', zorder=3)
        ax.text(x, y, str(prop['index']), color='white', ha='center', va='center',
                fontsize=7.5, zorder=4)
        label = ' '.join(times) if times else (prop.get('time') or {}).get('period', '')
        ax.text(x, y - 0.30, label, ha='center', va='top', fontsize=6, color='#44546A')
        here = set(visits[sid])
        fresh = [n for n in visits[sid] if n and n not in prev]
        if fresh:
            ax.text(x, y + 0.30, '、'.join(fresh[:2]), ha='center', va='bottom', fontsize=6.2)
        prev |= here
        if k + 1 < len(steps):
            nrow, ncol = divmod(k + 1, per_row)
            ax.plot([x, ncol * 1.6], [y, -nrow * 1.5], color='#C8CDD4', zorder=1, linewidth=1.0,
                    linestyle='-' if nrow == row else ':')
    rows = (len(steps) + per_row - 1) // per_row
    ax.set_xlim(-0.8, per_row * 1.6 - 0.2)
    ax.set_ylim(-(rows - 1) * 1.5 - 0.9, 1.0)
    ax.set_title('九寨沟官方一日游线路的图谱结构（%d 步，07:00—17:35，蓝点为步骤，上方为到过的景点）'
                 % len(steps), fontsize=8.5)
    ax.axis('off')
    fig.tight_layout()
    OUT6.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT6 / '图6-1_官方线路结构.png', bbox_inches='tight')
    plt.close(fig)

    types = Counter(e['type'] for e in graph['edges'])
    items = sorted(types.items(), key=lambda kv: kv[1])
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    ax.barh([k for k, _ in items], [v for _, v in items], color='#72B7B2')
    for i, (_, v) in enumerate(items):
        ax.text(v + 40, i, str(v), va='center', fontsize=6.5)
    ax.set_xlabel('关系条数', fontsize=7.5)
    ax.tick_params(labelsize=7)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    ax.set_title('图谱中的 %d 种关系（共 %d 条边）' % (len(types), len(graph['edges'])), fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT6 / '图6-2_关系类型分布.png', bbox_inches='tight')
    plt.close(fig)
    return len(steps), len({n for sid in steps for n in visits[sid]}), types


def main():
    rules = load('conditions.json')
    graph = load('graph.json')
    n, cats, auds = fig5(rules)
    steps, pois, types = fig6(graph)
    print(json.dumps({'rules': n, 'clause_categories': dict(cats),
                      'audience_buckets': dict(auds),
                      'official_steps': steps, 'official_route_pois': pois,
                      'edge_types': dict(types), 'figures': 4},
                     ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
